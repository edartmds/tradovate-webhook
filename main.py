import os
import logging
import json
import asyncio
import traceback
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from tradovate_api import TradovateClient
import uvicorn
import httpx
import hashlib

# 🔥 ENHANCED DUPLICATE DETECTION AND TRADE TRACKING
last_alert = {}  # {symbol: {"direction": "buy"/"sell", "timestamp": datetime, "alert_hash": str}}
completed_trades = {}  # {symbol: {"last_completed_direction": "buy"/"sell", "completion_time": datetime}}
active_orders = []  # Track active order IDs to manage cancellation
DUPLICATE_THRESHOLD_SECONDS = 300  # 5 minutes - ignore identical alerts within this timeframe
COMPLETED_TRADE_COOLDOWN = 600  # 10 minutes - prevent immediate identical trades after completion

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
logging.info(f"Loaded WEBHOOK_SECRET: {WEBHOOK_SECRET}")

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

log_file = os.path.join(LOG_DIR, "webhook_trades.log")
logging.basicConfig(
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ],
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

app = FastAPI()
client = TradovateClient()


@app.on_event("startup")
async def startup_event():
    logging.info("=== APPLICATION STARTING UP ===")
    try:
        await client.authenticate()
        logging.info(f"=== AUTHENTICATION SUCCESSFUL ===")
        logging.info(f"Account ID: {client.account_id}")
        logging.info(f"Account Spec: {client.account_spec}")
        logging.info(f"Access Token: {'***' if client.access_token else 'None'}")
          # Close any existing positions and cancel pending orders on startup to start clean
        logging.info("=== CLEANING UP EXISTING POSITIONS AND ORDERS ON STARTUP ===")
        try:
            # Close all positions first
            closed_positions = await client.close_all_positions()
            logging.info(f"Startup cleanup: Closed {len(closed_positions)} existing positions")
            
            # Cancel all pending orders
            cancelled_orders = await client.cancel_all_pending_orders()
            logging.info(f"Startup cleanup: Cancelled {len(cancelled_orders)} existing pending orders")
        except Exception as e:
            logging.warning(f"Startup cleanup failed (non-critical): {e}")
            
    except Exception as e:
        logging.error(f"=== AUTHENTICATION FAILED ===")
        logging.error(f"Error: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise


async def cancel_all_orders(symbol):
    # Cancel all open orders for the symbol, regardless of status, and double-check after
    list_url = f"https://demo-api.tradovate.com/v1/order/list"
    cancel_url = f"https://demo-api.tradovate.com/v1/order/cancel"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    async with httpx.AsyncClient() as http_client:
        # Repeat cancel attempts until no open orders remain (with a max retry limit)
        max_retries = 8
        for attempt in range(max_retries):
            resp = await http_client.get(list_url, headers=headers)
            resp.raise_for_status()
            orders = resp.json()
            # Cancel ALL orders for the symbol, regardless of status (except Filled/Cancelled/Rejected)
            open_orders = [o for o in orders if o.get("symbol") == symbol and o.get("status") not in ("Filled", "Cancelled", "Rejected")]
            if not open_orders:
                break
            for order in open_orders:
                oid = order.get("id")
                if oid:
                    try:
                        await http_client.post(f"{cancel_url}/{oid}", headers=headers)
                        logging.info(f"Cancelled order {oid} for {symbol} (status: {order.get('status')})")
                    except Exception as e:
                        logging.error(f"Failed to cancel order {oid} for {symbol}: {e}")
            await asyncio.sleep(0.5)
        # Final check and log if any remain
        resp = await http_client.get(list_url, headers=headers)
        resp.raise_for_status()
        orders = resp.json()
        open_orders = [o for o in orders if o.get("symbol") == symbol and o.get("status") not in ("Filled", "Cancelled", "Rejected")]
        if open_orders:
            logging.error(f"After repeated cancel attempts, still found open orders for {symbol}: {[o.get('id') for o in open_orders]} (statuses: {[o.get('status') for o in open_orders]})")

async def flatten_position(symbol):
    url = f"https://demo-api.tradovate.com/v1/position/closeposition"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    async with httpx.AsyncClient() as http_client:
        await http_client.post(url, headers=headers, json={"symbol": symbol})

async def wait_until_no_open_orders(symbol, timeout=10):
    """
    Poll Tradovate until there are no open orders for the symbol, or until timeout (seconds).
    """
    url = f"https://demo-api.tradovate.com/v1/order/list"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    start = asyncio.get_event_loop().time()
    while True:
        async with httpx.AsyncClient() as http_client:
            resp = await http_client.get(url, headers=headers)
            resp.raise_for_status()
            orders = resp.json()
            open_orders = [o for o in orders if o.get("symbol") == symbol and o.get("status") in ("Working", "Accepted")]
            if not open_orders:
                return
        if asyncio.get_event_loop().time() - start > timeout:
            logging.warning(f"Timeout waiting for all open orders to clear for {symbol}.")
            return
        await asyncio.sleep(0.5)

def parse_alert_to_tradovate_json(alert_text: str, account_id: int) -> dict:
    logging.info(f"Raw alert text: {alert_text}")
    parsed_data = {}
    if alert_text.startswith("="):
        try:
            json_part, remaining_text = alert_text[1:].split("\n", 1)
            json_data = json.loads(json_part)
            parsed_data.update(json_data)
            alert_text = remaining_text
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Error parsing JSON-like structure: {e}")

    for line in alert_text.split("\n"):
        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            parsed_data[key] = value
            logging.info(f"Parsed {key} = {value}")
        elif line.strip().upper() in ["BUY", "SELL"]:
            parsed_data["action"] = line.strip().capitalize()
            logging.info(f"Parsed action = {parsed_data['action']}")

    logging.info(f"Complete parsed alert data: {parsed_data}")

    required_fields = ["symbol", "action"]
    for field in required_fields:
        if field not in parsed_data or not parsed_data[field]:
            raise ValueError(f"Missing or invalid field: {field}")

    for target in ["T1", "STOP", "PRICE"]:
        if target in parsed_data:
            parsed_data[target] = float(parsed_data[target])
            logging.info(f"Converted {target} to float: {parsed_data[target]}")

    return parsed_data

def hash_alert(data: dict) -> str:
    """Generate a unique hash for an alert to detect duplicates."""
    # Only include essential trading fields for duplicate detection
    essential_fields = {
        "symbol": data.get("symbol"),
        "action": data.get("action"), 
        "PRICE": data.get("PRICE"),
        "T1": data.get("T1"),
        "STOP": data.get("STOP")
    }
    alert_string = json.dumps(essential_fields, sort_keys=True)
    return hashlib.sha256(alert_string.encode()).hexdigest()

def is_duplicate_alert(symbol: str, action: str, data: dict) -> bool:
    """
    🔥 CRITICAL DUPLICATE DETECTION: Check if this alert is a duplicate or too soon after completion.
    
    Returns True if:
    1. Identical alert received within DUPLICATE_THRESHOLD_SECONDS
    2. Same direction trade completed recently (within COMPLETED_TRADE_COOLDOWN)
    3. Alert hash matches recent alert
    """
    current_time = datetime.now()
    alert_hash = hash_alert(data)
    
    # Check 1: Recent identical alert
    if symbol in last_alert:
        last_alert_data = last_alert[symbol]
        time_diff = (current_time - last_alert_data["timestamp"]).total_seconds()
        
        # Same hash within threshold = duplicate
        if (last_alert_data.get("alert_hash") == alert_hash and 
            time_diff < DUPLICATE_THRESHOLD_SECONDS):
            logging.warning(f"🚫 DUPLICATE ALERT DETECTED for {symbol} {action}")
            logging.warning(f"🚫 Same alert hash received {time_diff:.1f} seconds ago")
            return True
            
        # Same direction within threshold = too frequent
        if (last_alert_data.get("direction", "").lower() == action.lower() and 
            time_diff < DUPLICATE_THRESHOLD_SECONDS):
            logging.warning(f"🚫 FREQUENT ALERT DETECTED for {symbol} {action}")
            logging.warning(f"🚫 Same direction alert received {time_diff:.1f} seconds ago")
            return True
    
    # Check 2: Recently completed trade in same direction
    if symbol in completed_trades:
        completed_data = completed_trades[symbol]
        time_since_completion = (current_time - completed_data["completion_time"]).total_seconds()
        
        if (completed_data.get("last_completed_direction", "").lower() == action.lower() and
            time_since_completion < COMPLETED_TRADE_COOLDOWN):
            logging.warning(f"🚫 POST-COMPLETION DUPLICATE for {symbol} {action}")
            logging.warning(f"🚫 Same direction trade completed {time_since_completion:.1f} seconds ago")
            logging.warning(f"🚫 Waiting {COMPLETED_TRADE_COOLDOWN - time_since_completion:.1f} more seconds")
            return True
    
    # Not a duplicate - update tracking
    last_alert[symbol] = {
        "direction": action.lower(),
        "timestamp": current_time,
        "alert_hash": alert_hash
    }
    
    logging.info(f"✅ UNIQUE ALERT ACCEPTED for {symbol} {action}")
    return False

def mark_trade_completed(symbol: str, direction: str):
    """Mark a trade as completed to prevent immediate duplicates."""
    completed_trades[symbol] = {
        "last_completed_direction": direction.lower(),
        "completion_time": datetime.now()
    }
    logging.info(f"🏁 TRADE COMPLETION MARKED: {symbol} {direction}")
    
def cleanup_old_tracking_data():
    """Clean up old tracking data to prevent memory leaks."""
    current_time = datetime.now()
    cutoff_time = current_time - timedelta(seconds=max(DUPLICATE_THRESHOLD_SECONDS, COMPLETED_TRADE_COOLDOWN) * 2)
    
    # Clean old alerts
    symbols_to_remove = []
    for symbol, data in last_alert.items():
        if data["timestamp"] < cutoff_time:
            symbols_to_remove.append(symbol)
    for symbol in symbols_to_remove:
        del last_alert[symbol]
        
    # Clean old completed trades
    symbols_to_remove = []
    for symbol, data in completed_trades.items():
        if data["completion_time"] < cutoff_time:
            symbols_to_remove.append(symbol)
    for symbol in symbols_to_remove:
        del completed_trades[symbol]

# Direct API function to place a stop loss order (DEPRECATED - using OCO/OSO instead)
async def place_stop_loss_order_legacy(stop_order_data):
    """
    DEPRECATED: This function is kept for reference only.
    We now use OCO/OSO bracket orders instead of manual stop loss placement.
    """
    logging.warning("DEPRECATED: place_stop_loss_order_legacy called - use OCO/OSO instead")
    return None, "DEPRECATED: Use OCO/OSO bracket orders instead"
async def monitor_all_orders(order_tracking, symbol, stop_order_data=None):
    """
    🔥 ENHANCED ORDER MONITORING with trade completion detection
    
    Monitor all orders and manage their relationships:
    - If ENTRY is filled, place the STOP order and keep TP active
    - If TP is filled, cancel STOP and mark trade as completed
    - If STOP is filled, cancel TP and mark trade as completed
    - Track completion to prevent immediate duplicate alerts
    """
    logging.info(f"Starting comprehensive order monitoring for {symbol}")
    entry_filled = False
    stop_placed = False
    monitoring_start_time = asyncio.get_event_loop().time()
    max_monitoring_time = 3600  # 1 hour timeout
    
    # Extra check for stop_order_data
    if not stop_order_data:
        logging.error("CRITICAL: No stop_order_data provided when starting monitoring")
    else:
        logging.info(f"Will use this STOP data when entry fills: {stop_order_data}")
    
    # Check orders every second
    poll_interval = 1
    
    while True:
        try:
            headers = {"Authorization": f"Bearer {client.access_token}"}
            active_orders = {}
            logging.info(f"Order tracking state: {order_tracking}")
            
            # Check status of all tracked orders
            for label, order_id in order_tracking.items():
                if order_id is None:
                    continue
                    
                url = f"https://demo-api.tradovate.com/v1/order/{order_id}"
                async with httpx.AsyncClient() as http_client:
                    response = await http_client.get(url, headers=headers)
                    response.raise_for_status()
                    order_status = response.json()
                    
                status = order_status.get("status")
                
                # Special focus on ENTRY order status
                if label == "ENTRY":
                    logging.info(f"CRITICAL: ENTRY order (ID: {order_id}) status: {status}")
                else:
                    logging.info(f"Order {label} (ID: {order_id}) status: {status}")
                
                if status and status.lower() == "filled":
                    # CRITICAL: If ENTRY is filled and we haven't placed stop loss yet
                    if label == "ENTRY" and not entry_filled:
                        entry_filled = True

                        # Prepare the OSO payload for stop loss and take profit
                        if stop_order_data and "T1" in stop_order_data:
                            oso_payload = {
                                "accountSpec": client.account_spec,
                                "accountId": client.account_id,
                                "action": stop_order_data.get("action"),
                                "symbol": stop_order_data.get("symbol"),
                                "orderQty": stop_order_data.get("orderQty", 1),
                                "orderType": "Stop",
                                "price": stop_order_data.get("stopPrice"),
                                "isAutomated": True,
                                "bracket1": {
                                    "action": "Sell" if stop_order_data.get("action") == "Buy" else "Buy",
                                    "orderType": "Limit",
                                    "price": stop_order_data.get("T1"),
                                    "timeInForce": "GTC"
                                }
                            }

                            try:
                                # Place the OSO order
                                async with httpx.AsyncClient() as http_client:
                                    response = await http_client.post(
                                        f"https://demo-api.tradovate.com/v1/order/placeOSO",
                                        headers={"Authorization": f"Bearer {client.access_token}", "Content-Type": "application/json"},
                                        json=oso_payload
                                    )
                                    response.raise_for_status()
                                    oso_result = response.json()

                                    if "orderId" in oso_result:
                                        logging.info(f"OSO order placed successfully: {oso_result}")
                                        stop_placed = True
                                    else:
                                        raise ValueError(f"Failed to place OSO order: {oso_result}")
                            except Exception as e:
                                logging.error(f"Error placing OSO order: {e}")
                        else:
                            logging.error("Missing stop_order_data or T1 for OSO placement.")
                    
                    # 🔥 CRITICAL: If TP1 is filled, cancel the stop loss and mark trade completion
                    elif label == "TP1" and entry_filled:
                        logging.info(f"🏁 TP1 order filled! Trade completed successfully - PROFIT TARGET HIT")
                        trade_direction = stop_order_data.get("action", "unknown") if stop_order_data else "unknown"
                        
                        # Mark trade as completed to prevent immediate duplicates
                        mark_trade_completed(symbol, trade_direction)
                        logging.info(f"✅ Trade completion recorded: {symbol} {trade_direction} via Take Profit")
                        
                        if order_tracking.get("STOP"):
                            cancel_url = f"https://demo-api.tradovate.com/v1/order/cancel/{order_tracking['STOP']}"
                            try:
                                async with httpx.AsyncClient() as http_client:
                                    resp = await http_client.post(cancel_url, headers=headers)
                                    if resp.status_code == 200:
                                        logging.info(f"STOP order {order_tracking['STOP']} cancelled after TP1 fill.")
                                    else:
                                        logging.warning(f"Failed to cancel STOP order after TP1 fill. Status: {resp.status_code}")
                            except Exception as e:
                                logging.error(f"Exception while cancelling STOP order after TP1 fill: {e}")
                        return  # Exit monitoring
                    
                    # 🔥 CRITICAL: If stop loss is filled, cancel the take profit and mark trade completion
                    elif label == "STOP" and entry_filled:
                        logging.info(f"🛑 STOP order filled! Trade completed - STOP LOSS HIT")
                        trade_direction = stop_order_data.get("action", "unknown") if stop_order_data else "unknown"
                        
                        # Mark trade as completed to prevent immediate duplicates
                        mark_trade_completed(symbol, trade_direction)
                        logging.info(f"✅ Trade completion recorded: {symbol} {trade_direction} via Stop Loss")
                        
                        if order_tracking.get("TP1"):
                            cancel_url = f"https://demo-api.tradovate.com/v1/order/cancel/{order_tracking['TP1']}"
                            try:
                                async with httpx.AsyncClient() as http_client:
                                    resp = await http_client.post(cancel_url, headers=headers)
                                    if resp.status_code == 200:
                                        logging.info(f"TP1 order {order_tracking['TP1']} cancelled after STOP fill.")
                                    else:
                                        logging.warning(f"Failed to cancel TP1 order after STOP fill. Status: {resp.status_code}")
                            except Exception as e:
                                logging.error(f"Exception while cancelling TP1 order after STOP fill: {e}")
                        return  # Exit monitoring
                        
                elif status in ["Working", "Accepted"]:
                    active_orders[label] = order_id
                    
            # Check if we've been monitoring too long
            if asyncio.get_event_loop().time() - monitoring_start_time > max_monitoring_time:
                logging.warning(f"Order monitoring timeout reached for {symbol}. Stopping.")
                return
            
            # If no active orders remain, stop monitoring
            if not active_orders:
                logging.info("No active orders remaining. Stopping monitoring.")
                return
            
            # Check 2x per second if entry has been filled
            if not entry_filled:
                poll_interval = 0.5
            else:
                poll_interval = 1
                
            await asyncio.sleep(poll_interval)
            
        except Exception as e:
            logging.error(f"Error in order monitoring: {e}")
            await asyncio.sleep(5)


@app.post("/webhook")
async def webhook(req: Request):
    logging.info("=== WEBHOOK ENDPOINT HIT ===")
    try:
        # Parse the incoming request
        content_type = req.headers.get("content-type")
        raw_body = await req.body()
        logging.info(f"Content-Type: {content_type}")
        logging.info(f"Raw body: {raw_body.decode('utf-8')}")

        if content_type == "application/json":
            data = await req.json()
        elif content_type.startswith("text/plain"):
            text_data = raw_body.decode("utf-8")
            data = parse_alert_to_tradovate_json(text_data, client.account_id)
        else:
            logging.error(f"Unsupported content type: {content_type}")
            raise HTTPException(status_code=400, detail="Unsupported content type")

        logging.info(f"=== PARSED ALERT DATA: {data} ===")        # Extract required fields
        symbol = data.get("symbol")
        action = data.get("action")
        price = data.get("PRICE")
        t1 = data.get("T1")
        stop = data.get("STOP")

        logging.info(f"Extracted fields - Symbol: {symbol}, Action: {action}, Price: {price}, T1: {t1}, Stop: {stop}")

        if not all([symbol, action, price, t1, stop]):
            missing = [k for k, v in {"symbol": symbol, "action": action, "PRICE": price, "T1": t1, "STOP": stop}.items() if not v]
            logging.error(f"Missing required fields: {missing}")
            raise HTTPException(status_code=400, detail=f"Missing required fields: {missing}")

        # Map TradingView symbol to Tradovate symbol
        if symbol == "CME_MINI:NQ1!" or symbol == "NQ1!":
            symbol = "NQM5"
            logging.info(f"Mapped symbol to: {symbol}")
        
        # 🔥 CRITICAL DUPLICATE DETECTION - Check before any trading actions
        logging.info("🔍 === CHECKING FOR DUPLICATE ALERTS ===")
        cleanup_old_tracking_data()  # Clean up old data first
        
        if is_duplicate_alert(symbol, action, data):
            logging.warning(f"🚫 DUPLICATE/FREQUENT ALERT REJECTED: {symbol} {action}")
            logging.warning(f"🚫 Reason: Too similar to recent alert or completed trade")
            return {
                "status": "rejected", 
                "reason": "duplicate_alert",
                "message": f"Duplicate or too frequent alert for {symbol} {action}"
            }
        
        logging.info(f"✅ ALERT APPROVED: {symbol} {action} - Proceeding with trade")
        
        # 🔥 TRADE COMPLETION DETECTION: Check if this is right after a successful completion
        # If we just closed positions and this is the same direction, it might be a completion duplicate
        try:
            current_positions = await client.get_positions()
            if not current_positions:  # No positions = recently completed trade
                logging.info("🔍 No open positions detected - checking for post-completion duplicate")
                # Additional check for completion-based duplicates
                if symbol in completed_trades:
                    last_completed = completed_trades[symbol]
                    time_since_completion = (datetime.now() - last_completed["completion_time"]).total_seconds()
                    if (last_completed.get("last_completed_direction") == action.lower() and 
                        time_since_completion < 180):  # 3 minutes post-completion protection
                        logging.warning(f"🚫 POST-COMPLETION DUPLICATE REJECTED: {symbol} {action}")
                        logging.warning(f"🚫 Same direction completed {time_since_completion:.1f}s ago")
                        return {
                            "status": "rejected",
                            "reason": "post_completion_duplicate", 
                            "message": f"Trade completed recently, preventing duplicate"
                        }
        except Exception as e:
            logging.warning(f"Position check failed during duplicate detection: {e}")
            # Continue anyway - don't let position check errors block trading
            logging.warning(f"🚫 DUPLICATE/FREQUENT ALERT REJECTED: {symbol} {action}")
            logging.warning(f"🚫 Reason: Too similar to recent alert or completed trade")
            return {
                "status": "rejected", 
                "reason": "duplicate_alert",
                "message": f"Duplicate or too frequent alert for {symbol} {action}"
            }
        
        logging.info(f"✅ ALERT APPROVED: {symbol} {action} - Proceeding with trade")
        
        # 🔥 TRADE COMPLETION DETECTION: Check if this is right after a successful completion
        # If we just closed positions and this is the same direction, it might be a completion duplicate
        try:
            current_positions = await client.get_positions()
            if not current_positions:  # No positions = recently completed trade
                logging.info("🔍 No open positions detected - checking for post-completion duplicate")
                # Additional check for completion-based duplicates
                if symbol in completed_trades:
                    last_completed = completed_trades[symbol]
                    time_since_completion = (datetime.now() - last_completed["completion_time"]).total_seconds()
                    if (last_completed.get("last_completed_direction") == action.lower() and 
                        time_since_completion < 180):  # 3 minutes post-completion protection
                        logging.warning(f"🚫 POST-COMPLETION DUPLICATE REJECTED: {symbol} {action}")
                        logging.warning(f"🚫 Same direction completed {time_since_completion:.1f}s ago")
                        return {
                            "status": "rejected",
                            "reason": "post_completion_duplicate", 
                            "message": f"Trade completed recently, preventing duplicate"
                        }
        except Exception as e:
            logging.warning(f"Position check failed during duplicate detection: {e}")
            # Continue anyway - don't let position check errors block trading# STEP 1: Close all existing positions to prevent over-leveraging  
        logging.info("🔥🔥🔥 === CLOSING ALL EXISTING POSITIONS === 🔥🔥🔥")
        try:
            success = await client.force_close_all_positions_immediately()
            if success:
                logging.info("✅ All existing positions successfully closed")
            else:
                logging.error("❌ CRITICAL: Failed to close all positions - proceeding anyway")
        except Exception as e:
            logging.error(f"❌ CRITICAL ERROR closing positions: {e}")
            # Continue anyway - user wants new orders placed regardless

        # STEP 2: Cancel all existing pending orders to prevent over-leveraging
        logging.info("=== CANCELLING ALL PENDING ORDERS ===")
        try:
            cancelled_orders = await client.cancel_all_pending_orders()
            logging.info(f"Successfully cancelled {len(cancelled_orders)} pending orders")
        except Exception as e:
            logging.warning(f"Failed to cancel some orders: {e}")
            # Continue with new order placement even if cancellation partially fails        # STEP 3: Place entry order with automatic bracket orders (OSO)
        logging.info(f"=== PLACING OSO BRACKET ORDER FOR EXACT POSITIONING ===")
        logging.info(f"Symbol: {symbol}, Entry: {price}, TP: {t1}, SL: {stop}")
        
        # Determine opposite action for take profit and stop loss
        opposite_action = "Sell" if action.lower() == "buy" else "Buy"
        
        # For OSO orders, we need to determine entry strategy based on price direction
        # If BUY and current price is below target, use Limit order
        # If BUY and current price is above target, use Stop order
        # This ensures proper entry execution
        
        # OSO payload with proper Tradovate API structure
        oso_payload = {
            "accountSpec": client.account_spec,
            "accountId": client.account_id,
            "action": action.capitalize(),  # "Buy" or "Sell"
            "symbol": symbol,
            "orderQty": 1,
            "orderType": "Limit",  # Use Limit for exact price entry
            "price": price,        # Entry at EXACT PRICE level
            "timeInForce": "GTC",
            "isAutomated": True,
            # Take Profit bracket (bracket1)
            "bracket1": {
                "accountSpec": client.account_spec,
                "accountId": client.account_id,
                "action": opposite_action,
                "symbol": symbol,
                "orderQty": 1,
                "orderType": "Limit",
                "price": t1,
                "timeInForce": "GTC",
                "isAutomated": True
            },
            # Stop Loss bracket (bracket2)
            "bracket2": {
                "accountSpec": client.account_spec,
                "accountId": client.account_id,
                "action": opposite_action,
                "symbol": symbol,
                "orderQty": 1,
                "orderType": "Stop",
                "stopPrice": stop,
                "timeInForce": "GTC",
                "isAutomated": True
            }
        }
        
        logging.info(f"=== OSO PAYLOAD ===")
        logging.info(f"{json.dumps(oso_payload, indent=2)}")
          # STEP 4: Place OSO bracket order
        logging.info("=== PLACING OSO BRACKET ORDER ===")
        try:
            oso_result = await client.place_oso_order(oso_payload)
            logging.info(f"✅ OSO BRACKET ORDER PLACED SUCCESSFULLY")
            logging.info(f"OSO Result: {oso_result}")
            
            # 🔥 MARK SUCCESSFUL TRADE PLACEMENT - This helps prevent immediate duplicates
            # When this trade completes (hits TP or SL), we'll prevent duplicate signals for a period
            logging.info(f"📝 Recording successful trade placement: {symbol} {action}")
            # Note: We mark completion when the trade actually completes, not just when placed
            
            return {"status": "success", "order": oso_result}
        except Exception as e:
            logging.error(f"❌ OSO placement failed: {e}")
            # Log the detailed error for debugging
            import traceback
            logging.error(f"OSO Error traceback: {traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"OSO order placement failed: {str(e)}")

    except Exception as e:
        logging.error(f"=== ERROR IN WEBHOOK ===")
        logging.error(f"Error: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
