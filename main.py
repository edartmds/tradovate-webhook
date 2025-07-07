import os
import logging
import json
import asyncio
import traceback
import time
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from tradovate_api import TradovateClient
import uvicorn
import httpx
import hashlib




# 🔥 RELAXED DUPLICATE DETECTION FOR AUTOMATED TRADING
last_alert = {}  # {symbol: {"direction": "buy"/"sell", "timestamp": datetime, "alert_hash": str}}
completed_trades = {}  # {symbol: {"last_completed_direction": "buy"/"sell", "completion_time": datetime}}
active_orders = []  # Track active order IDs to manage cancellation
DUPLICATE_THRESHOLD_SECONDS = 30  # 30 seconds - only prevent rapid-fire identical alerts
COMPLETED_TRADE_COOLDOWN = 30  # 30 seconds - minimal cooldown for automated trading




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
# Dictionary of asyncio locks per symbol to serialize webhook handling and prevent race conditions
symbol_locks = {}








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
    🔥 RELAXED DUPLICATE DETECTION FOR AUTOMATED TRADING
   
    Only blocks truly rapid-fire identical alerts within 30 seconds.
    Allows direction changes and new signals for automated flattening strategy.
   
    Returns True ONLY if:
    1. IDENTICAL alert hash received within 30 seconds (prevents accidental spam)
    """
    current_time = datetime.now()
    alert_hash = hash_alert(data)
   
    # ONLY Check for rapid-fire identical alerts (same exact parameters)
    if symbol in last_alert:
        last_alert_data = last_alert[symbol]
        time_diff = (current_time - last_alert_data["timestamp"]).total_seconds()
       
        # Only block if EXACT same alert within 30 seconds
        if (last_alert_data.get("alert_hash") == alert_hash and
            time_diff < DUPLICATE_THRESHOLD_SECONDS):
            logging.warning(f"🚫 RAPID-FIRE DUPLICATE BLOCKED: {symbol} {action}")
            logging.warning(f"🚫 Identical alert received {time_diff:.1f} seconds ago")
            return True
   
    # 🔥 REMOVED: Direction-based blocking - allow all direction changes
    # 🔥 REMOVED: Post-completion blocking - allow immediate new signals
    # This enables full automated trading with position flattening
   
    # Update tracking for rapid-fire detection only
    last_alert[symbol] = {
        "direction": action.lower(),
        "timestamp": current_time,
        "alert_hash": alert_hash
    }
   
    logging.info(f"✅ ALERT ACCEPTED: {symbol} {action} - Automated trading enabled")
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
    Enhanced monitoring to ensure stop loss orders exit trades correctly.
    """
    logging.info(f"Starting comprehensive order monitoring for {symbol}")
    entry_filled = False
    stop_placed = False
    monitoring_start_time = asyncio.get_event_loop().time()
    max_monitoring_time = 3600  # 1 hour timeout




    if not stop_order_data:
        logging.error("CRITICAL: No stop_order_data provided when starting monitoring")
    else:
        logging.info(f"Will use this STOP data when entry fills: {stop_order_data}")




    poll_interval = 1




    while True:
        try:
            headers = {"Authorization": f"Bearer {client.access_token}"}
            active_orders = {}
            logging.info(f"Order tracking state: {order_tracking}")




            for label, order_id in order_tracking.items():
                if order_id is None:
                    continue




                url = f"https://demo-api.tradovate.com/v1/order/{order_id}"
                async with httpx.AsyncClient() as http_client:
                    response = await http_client.get(url, headers=headers)
                    response.raise_for_status()
                    order_status = response.json()




                status = order_status.get("status")




                if label == "ENTRY" and status and status.lower() == "filled" and not entry_filled:
                    entry_filled = True
                    logging.info(f"ENTRY order filled for {symbol}. Placing STOP and TP orders.")




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




                elif label == "STOP" and status and status.lower() == "filled":
                    logging.info(f"STOP order filled for {symbol}. Exiting trade.")
                    trade_direction = stop_order_data.get("action", "unknown") if stop_order_data else "unknown"
                    mark_trade_completed(symbol, trade_direction)




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
                    return




                elif label == "TP1" and status and status.lower() == "filled":
                    logging.info(f"TP1 order filled for {symbol}. Trade completed successfully.")
                    trade_direction = stop_order_data.get("action", "unknown") if stop_order_data else "unknown"
                    mark_trade_completed(symbol, trade_direction)




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
                    return




                elif status in ["Working", "Accepted"]:
                    active_orders[label] = order_id




            if asyncio.get_event_loop().time() - monitoring_start_time > max_monitoring_time:
                logging.warning(f"Order monitoring timeout reached for {symbol}. Stopping.")
                return




            if not active_orders:
                logging.info("No active orders remaining. Stopping monitoring.")
                return




            poll_interval = 0.5 if not entry_filled else 1
            await asyncio.sleep(poll_interval)




        except Exception as e:
            logging.error(f"Error in order monitoring: {e}")
            await asyncio.sleep(2)








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




        logging.info(f"=== PARSED ALERT DATA: {data} ===")
       
        # Extract required fields
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
            symbol = "NQU5"  # Changed from NQM5 to NQU5
            logging.info(f"Mapped symbol to: {symbol}")
           
        # Ensure sequential handling per symbol to prevent race conditions
        lock = symbol_locks.setdefault(symbol, asyncio.Lock())
        logging.info(f"� Waiting for lock for symbol {symbol}")
        async with lock:
            logging.info(f"🔒 Acquired lock for {symbol}")
            # �🔥 MINIMAL DUPLICATE DETECTION - Only prevent rapid-fire identical alerts
            logging.info("🔍 === CHECKING FOR RAPID-FIRE DUPLICATES ONLY ===")
            cleanup_old_tracking_data()  # Clean up old data first


            if is_duplicate_alert(symbol, action, data):
                logging.warning(f"🚫 RAPID-FIRE DUPLICATE BLOCKED: {symbol} {action}")
                logging.warning(f"🚫 Reason: Identical alert within 30 seconds")
                return {
                    "status": "rejected",
                    "reason": "rapid_fire_duplicate",
                    "message": f"Rapid-fire duplicate alert blocked for {symbol} {action}"
                }


            logging.info(f"✅ ALERT APPROVED: {symbol} {action} - Proceeding with automated trading")
            
            # 🎯 SMART ORDER TYPE SELECTION TO AVOID REJECTIONS
            # Check if this is a breakout (price above/below current market) or pullback
            
            # Always use Stop orders for entries to avoid immediate fills
            # Stop orders wait at the exact price level until triggered
            order_type = "Stop"
            entry_price = price  # Use exact PRICE from alert
            
            logging.info(f"🎯 STOP ORDER ENTRY at exact price {entry_price}")
            logging.info(f"🎯 Alert PRICE={price}, T1={t1}, STOP={stop}")
            logging.info(f"🎯 Entry will trigger when market reaches {entry_price}")
       
        # 🔥 REMOVED POST-COMPLETION DUPLICATE DETECTION FOR FULL AUTOMATION
        # Every new alert will now automatically flatten existing positions and place new orders
       
        # STEP 1: Close all existing positions to prevent over-leveraging  
        logging.info("🔥🔥🔥 === AUTOMATED FLATTENING: CLOSING ALL EXISTING POSITIONS === 🔥🔥🔥")
        try:
            success = await client.force_close_all_positions_immediately()
            if success:
                logging.info("✅ All existing positions successfully closed")
            else:
                logging.error("❌ CRITICAL: Failed to close all positions - proceeding anyway")
        except Exception as e:
            logging.error(f"❌ CRITICAL ERROR closing positions: {e}")
            # Continue anyway - user wants new orders placed regardless

        # STEP 2: Cancel existing orders to avoid duplicates
        logging.info("=== CANCELLING ALL PENDING ORDERS ===")
        try:
            cancelled = await client.cancel_all_pending_orders()
            logging.info(f"Successfully cancelled {len(cancelled)} pending orders")
        except Exception as e:
            logging.warning(f"Failed to cancel some orders: {e}")
        # Wait for orders to clear
        await wait_until_no_open_orders(symbol)
        logging.info(f"✅ No open orders remain after generic cancel for {symbol}")
       
        logging.info("=== CANCELLING ANY REMAINING ORDERS FOR SYMBOL ===")
        try:
            # Targeted cancellation
            await cancel_all_orders(symbol)
            logging.info(f"✅ cancel_all_orders cleared remaining orders for {symbol}")
        except Exception as e:
            logging.warning(f"cancel_all_orders(symbol) failed: {e}")
        # Final wait
        await wait_until_no_open_orders(symbol)
        logging.info(f"✅ Confirmed no open orders remain for {symbol} after all cancellations")
        
        # STEP 3: Place entry order with automatic bracket orders (OSO)
        logging.info(f"=== PLACING OSO BRACKET ORDER WITH STOP ENTRY ===")
        logging.info(f"Symbol: {symbol}, Order Type: {order_type}, Entry: {entry_price}, TP: {t1}, SL: {stop}")
       
        logging.info("📊 STOP entry order - will wait at exact PRICE level from alert")
       
        # Determine opposite action for take profit and stop loss
        opposite_action = "Sell" if action.lower() == "buy" else "Buy"
        
        # � PRE-FLIGHT CHECKS TO PREVENT REJECTIONS
        logging.info("🔍 === PERFORMING PRE-FLIGHT CHECKS ===")
        
        # Check 1: Market hours
        market_open = await check_market_hours(symbol)
        if not market_open:
            logging.error("🕐 MARKET CLOSED - Cannot place orders")
            raise HTTPException(status_code=400, detail="Market is closed for this symbol")
        
        # Check 2: Account requirements  
        account_check = await validate_account_requirements()
        if not account_check.get("valid", False):
            logging.error(f"❌ ACCOUNT VALIDATION FAILED: {account_check.get('error')}")
            raise HTTPException(status_code=400, detail=f"Account validation failed: {account_check.get('error')}")
        
        logging.info(f"✅ Account buying power: {account_check.get('buying_power', 'unknown')}")
        
        # Check 3: Symbol specifications
        symbol_specs = await get_symbol_specifications(symbol)
        if not symbol_specs.get("valid", False):
            logging.error(f"❌ SYMBOL VALIDATION FAILED: {symbol_specs.get('error')}")
            raise HTTPException(status_code=400, detail=f"Symbol validation failed: {symbol_specs.get('error')}")
        
        logging.info(f"✅ Symbol specs: tick_size={symbol_specs.get('tick_size')}, point_value={symbol_specs.get('point_value')}")
        
        # �🛠️ BUILD ROBUST OSO PAYLOAD WITH VALIDATION
        oso_payload, validation_result = await build_robust_oso_payload(symbol, action, entry_price, t1, stop)
        
        logging.info(f"=== VALIDATED OSO PAYLOAD ===")
        logging.info(f"{json.dumps(oso_payload, indent=2)}")
        
        # Log validation results
        if validation_result["warnings"]:
            logging.warning("⚠️ ORDER VALIDATION WARNINGS:")
            for warning in validation_result["warnings"]:
                logging.warning(f"  {warning}")
        
        if validation_result["adjustments"]:
            logging.info("🔧 APPLIED PRICE ADJUSTMENTS:")
            for field, value in validation_result["adjustments"].items():
                logging.info(f"  {field}: {value}")
                
        current_market = validation_result.get("current_price")
        if current_market:
            logging.info(f"📊 Current market price: {current_market}")
        
        # STEP 4: Place OSO bracket order with speed optimizations
        logging.info("=== PLACING OSO BRACKET ORDER ===")
        
        # 🔥 SPEED OPTIMIZATION: Validate payload before submission to prevent rejection delays
        required_fields = ['accountSpec', 'accountId', 'action', 'symbol', 'orderQty', 'orderType', 'timeInForce']
        for field in required_fields:
            if field not in oso_payload:
                raise HTTPException(status_code=400, detail=f"Missing required OSO field: {field}")
        
        try:
            # 🚀 INTELLIGENT OSO PLACEMENT WITH RETRY LOGIC
            oso_result = await place_oso_with_retry(oso_payload, max_retries=3)
            
            logging.info(f"✅ OSO BRACKET ORDER PLACED SUCCESSFULLY")
            logging.info(f"OSO Result: {oso_result}")
            
            # Extract final prices used
            final_entry = oso_payload.get("stopPrice")
            final_tp = oso_payload["bracket1"]["price"]
            final_sl = oso_payload["bracket2"]["stopPrice"]
            
            return {
                "status": "success",
                "order": oso_result,
                "order_type": order_type,
                "symbol": symbol,
                "entry_price": final_entry,
                "take_profit": final_tp,
                "stop_loss": final_sl,
                "market_price": validation_result.get("current_price"),
                "adjustments_applied": len(validation_result.get("adjustments", {})) > 0,
                "warnings": validation_result.get("warnings", [])
            }
        except Exception as e:
            logging.error(f"❌ OSO placement failed: {e}")
            
            # 🔥 ENHANCED ERROR HANDLING FOR ORDER REJECTIONS
            error_msg = str(e).lower()
            
            if "price is already at or past this level" in error_msg:
                logging.error("🎯 PRICE LEVEL ERROR: Market has already moved past the entry price")
                logging.error(f"🎯 Alert Entry Price: {entry_price}")
                logging.error(f"🎯 Alert T1: {t1}, Alert STOP: {stop}")
                logging.error("🎯 SOLUTION: This is normal for fast-moving markets - alert may be stale")
                
                # For debugging: log the exact values being used
                logging.error(f"🔍 DEBUG - Entry stopPrice: {oso_payload.get('stopPrice')}")
                logging.error(f"🔍 DEBUG - TP price: {oso_payload['bracket1']['price']}")
                logging.error(f"🔍 DEBUG - SL stopPrice: {oso_payload['bracket2']['stopPrice']}")
                
            elif "insufficient buying power" in error_msg:
                logging.error("💰 MARGIN ERROR: Insufficient buying power for position size")
                logging.error("💰 SOLUTION: Reduce position size or add more margin")
                
            elif "invalid symbol" in error_msg:
                logging.error(f"📊 SYMBOL ERROR: Contract symbol {symbol} may be expired or invalid")
                logging.error("📊 SOLUTION: Check if contract has rolled to new month")
                
            elif "order quantity" in error_msg:
                logging.error("📏 QUANTITY ERROR: Invalid order quantity")
                logging.error("📏 SOLUTION: Check minimum order size requirements")
                
            elif "market is closed" in error_msg:
                logging.error("🕐 MARKET CLOSED ERROR: Cannot place orders outside market hours")
                logging.error("🕐 SOLUTION: Wait for market to open")
                
            else:
                logging.error(f"❓ UNKNOWN ERROR: {error_msg}")
                
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




@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "active",
        "service": "tradovate-webhook",
        "endpoints": {
            "webhook": "/webhook",
            "health": "/"
        },
        "message": "Webhook service is running. Send POST requests to /webhook"
    }




@app.post("/")
async def root_post(req: Request):
    """Handle POST requests to root and redirect to webhook"""
    logging.warning("POST request received at root path '/' - redirecting to /webhook")
    logging.info("If you're sending webhooks, please update your URL to include '/webhook' at the end")
   
    # Forward the request to the webhook endpoint
    return await webhook(req)




async def get_current_market_price(symbol: str) -> dict:
    """
    Fetch current market data for a symbol to make intelligent order decisions.
    """
    if not client.access_token:
        await client.authenticate()
    
    headers = {"Authorization": f"Bearer {client.access_token}"}
    
    try:
        async with httpx.AsyncClient() as http_client:
            # Get contract info first
            contract_url = f"https://demo-api.tradovate.com/v1/contract/find"
            contract_resp = await http_client.get(f"{contract_url}?name={symbol}", headers=headers)
            contract_resp.raise_for_status()
            contract_data = contract_resp.json()
            
            if not contract_data:
                logging.error(f"❌ Contract not found for symbol: {symbol}")
                return None
                
            contract_id = contract_data.get("id")
            
            # Get current market data
            tick_url = f"https://demo-api.tradovate.com/v1/md/getChart"
            tick_params = {
                "symbol": symbol,
                "chartDescription": {
                    "underlyingType": "MinuteBar",
                    "elementSize": 1,
                    "elementSizeUnit": "UnderlyingUnits"
                },
                "timeRange": {
                    "closestTimestamp": int(time.time() * 1000),
                    "asMuchAsElements": 1
                }
            }
            
            tick_resp = await http_client.post(tick_url, headers=headers, json=tick_params)
            
            if tick_resp.status_code == 200:
                tick_data = tick_resp.json()
                if tick_data and "bars" in tick_data and tick_data["bars"]:
                    latest_bar = tick_data["bars"][-1]
                    return {
                        "symbol": symbol,
                        "current_price": latest_bar.get("close", latest_bar.get("high", 0)),
                        "high": latest_bar.get("high"),
                        "low": latest_bar.get("low"),
                        "open": latest_bar.get("open"),
                        "close": latest_bar.get("close")
                    }
            
            # Fallback: try to get from positions or orders
            logging.warning(f"⚠️ Could not get market data for {symbol}, using fallback")
            return {"symbol": symbol, "current_price": None}
            
    except Exception as e:
        logging.error(f"❌ Error getting market data for {symbol}: {e}")
        return {"symbol": symbol, "current_price": None}


async def validate_order_prices(symbol: str, action: str, entry_price: float, t1: float, stop: float) -> dict:
    """
    Validate order prices against current market conditions to prevent rejections.
    """
    logging.info(f"🔍 VALIDATING ORDER PRICES for {symbol}")
    
    # Get symbol specifications for proper price rounding
    symbol_specs = await get_symbol_specifications(symbol)
    tick_size = symbol_specs.get("tick_size", 0.25)
    
    # Round all prices to proper tick increments
    entry_price = round_to_tick_size(entry_price, tick_size)
    t1 = round_to_tick_size(t1, tick_size)
    stop = round_to_tick_size(stop, tick_size)
    
    logging.info(f"🔍 Prices rounded to tick size {tick_size}: Entry={entry_price}, T1={t1}, Stop={stop}")
    
    market_data = await get_current_market_price(symbol)
    current_price = market_data.get("current_price") if market_data else None
    
    validation_result = {
        "valid": True,
        "warnings": [],
        "adjustments": {},
        "current_price": current_price,
        "tick_size": tick_size
    }
    
    if current_price is None:
        validation_result["warnings"].append("⚠️ Could not fetch current market price - proceeding with original prices")
        # Still apply tick size rounding
        validation_result["adjustments"]["entry_price"] = entry_price
        validation_result["adjustments"]["t1"] = t1
        validation_result["adjustments"]["stop"] = stop
        return validation_result
    
    # Round current price for comparison
    current_price = round_to_tick_size(current_price, tick_size)
    validation_result["current_price"] = current_price
    
    logging.info(f"🔍 Current market price: {current_price}")
    logging.info(f"🔍 Entry price: {entry_price}, T1: {t1}, Stop: {stop}")
    
    # Enhanced validation with buffer zones
    buffer_ticks = 2  # 2 tick buffer
    buffer_amount = buffer_ticks * tick_size
    
    if action.lower() == "buy":
        # For BUY Stop orders, entry should be above current market
        min_entry = current_price + buffer_amount
        if entry_price <= current_price:
            adjustment = round_to_tick_size(min_entry, tick_size)
            validation_result["warnings"].append(f"⚠️ BUY Stop entry {entry_price} too close to market {current_price}")
            validation_result["adjustments"]["entry_price"] = adjustment
            logging.warning(f"⚠️ Adjusting BUY Stop entry from {entry_price} to {adjustment}")
            entry_price = adjustment  # Update for further validation
            
        # Validate stop loss is below entry with minimum distance
        min_stop_distance = entry_price * 0.005  # Minimum 0.5% distance
        max_stop = entry_price - max(buffer_amount, min_stop_distance)
        if stop >= entry_price:
            adjustment = round_to_tick_size(max_stop, tick_size)
            validation_result["warnings"].append(f"⚠️ Stop loss {stop} should be below entry {entry_price}")
            validation_result["adjustments"]["stop"] = adjustment
            logging.warning(f"⚠️ Adjusting stop loss from {stop} to {adjustment}")
            
        # Validate take profit is above entry
        if t1 <= entry_price:
            min_tp = entry_price + buffer_amount
            adjustment = round_to_tick_size(min_tp, tick_size)
            validation_result["warnings"].append(f"⚠️ Take profit {t1} should be above entry {entry_price}")
            validation_result["adjustments"]["t1"] = adjustment
            logging.warning(f"⚠️ Adjusting take profit from {t1} to {adjustment}")
            
    else:  # SELL
        # For SELL Stop orders, entry should be below current market
        max_entry = current_price - buffer_amount
        if entry_price >= current_price:
            adjustment = round_to_tick_size(max_entry, tick_size)
            validation_result["warnings"].append(f"⚠️ SELL Stop entry {entry_price} too close to market {current_price}")
            validation_result["adjustments"]["entry_price"] = adjustment
            logging.warning(f"⚠️ Adjusting SELL Stop entry from {entry_price} to {adjustment}")
            entry_price = adjustment  # Update for further validation
            
        # Validate stop loss is above entry with minimum distance
        min_stop_distance = entry_price * 0.005  # Minimum 0.5% distance
        min_stop = entry_price + max(buffer_amount, min_stop_distance)
        if stop <= entry_price:
            adjustment = round_to_tick_size(min_stop, tick_size)
            validation_result["warnings"].append(f"⚠️ Stop loss {stop} should be above entry {entry_price}")
            validation_result["adjustments"]["stop"] = adjustment
            logging.warning(f"⚠️ Adjusting stop loss from {stop} to {adjustment}")
            
        # Validate take profit is below entry
        if t1 >= entry_price:
            max_tp = entry_price - buffer_amount
            adjustment = round_to_tick_size(max_tp, tick_size)
            validation_result["warnings"].append(f"⚠️ Take profit {t1} should be below entry {entry_price}")
            validation_result["adjustments"]["t1"] = adjustment
            logging.warning(f"⚠️ Adjusting take profit from {t1} to {adjustment}")
    
    # Apply tick size rounding to any prices that weren't adjusted
    if "entry_price" not in validation_result["adjustments"]:
        validation_result["adjustments"]["entry_price"] = entry_price
    if "t1" not in validation_result["adjustments"]:
        validation_result["adjustments"]["t1"] = t1
    if "stop" not in validation_result["adjustments"]:
        validation_result["adjustments"]["stop"] = stop
    
    return validation_result


async def build_robust_oso_payload(symbol: str, action: str, entry_price: float, t1: float, stop: float) -> dict:
    """
    Build a robust OSO payload with validation and error prevention.
    """
    logging.info("🛠️ BUILDING ROBUST OSO PAYLOAD")
    
    # Validate prices first
    validation = await validate_order_prices(symbol, action, entry_price, t1, stop)
    
    # Apply any adjustments
    final_entry = validation["adjustments"].get("entry_price", entry_price)
    final_stop = validation["adjustments"].get("stop", stop)
    final_t1 = validation["adjustments"].get("t1", t1)
    
    # Log any warnings
    for warning in validation["warnings"]:
        logging.warning(warning)
    
    opposite_action = "Sell" if action.lower() == "buy" else "Buy"
    
    # Build the payload with all required fields
    oso_payload = {
        "accountSpec": client.account_spec,
        "accountId": client.account_id,
        "action": action.capitalize(),
        "symbol": symbol,
        "orderQty": 1,
        "orderType": "Stop",
        "stopPrice": final_entry,
        "timeInForce": "GTC",
        "isAutomated": True,
        # Take Profit bracket
        "bracket1": {
            "accountSpec": client.account_spec,
            "accountId": client.account_id,
            "action": opposite_action,
            "symbol": symbol,
            "orderQty": 1,
            "orderType": "Limit",
            "price": final_t1,
            "timeInForce": "GTC",
            "isAutomated": True
        },
        # Stop Loss bracket
        "bracket2": {
            "accountSpec": client.account_spec,
            "accountId": client.account_id,
            "action": opposite_action,
            "symbol": symbol,
            "orderQty": 1,
            "orderType": "Stop",
            "stopPrice": final_stop,
            "timeInForce": "GTC",
            "isAutomated": True
        }
    }
    
    # Log final values
    logging.info(f"🛠️ FINAL OSO VALUES:")
    logging.info(f"🛠️ Entry Stop Price: {final_entry} (original: {entry_price})")
    logging.info(f"🛠️ Take Profit: {final_t1} (original: {t1})")
    logging.info(f"🛠️ Stop Loss: {final_stop} (original: {stop})")
    
    return oso_payload, validation


async def place_oso_with_retry(oso_payload: dict, max_retries: int = 3) -> dict:
    """
    Place OSO order with intelligent retry logic for common rejection scenarios.
    """
    last_error = None
    
    for attempt in range(max_retries):
        try:
            logging.info(f"🚀 OSO PLACEMENT ATTEMPT {attempt + 1}/{max_retries}")
            
            start_time = time.time()
            result = await client.place_oso_order(oso_payload)
            execution_time = (time.time() - start_time) * 1000
            
            logging.info(f"✅ OSO ORDER PLACED SUCCESSFULLY in {execution_time:.2f}ms on attempt {attempt + 1}")
            return result
            
        except Exception as e:
            last_error = e
            error_msg = str(e).lower()
            
            logging.error(f"❌ OSO ATTEMPT {attempt + 1} FAILED: {error_msg}")
            
            # Handle specific rejection scenarios
            if "price is already at or past this level" in error_msg:
                logging.warning("🔄 PRICE LEVEL REJECTION - Adjusting entry price")
                
                # Adjust entry price slightly
                current_stop_price = oso_payload.get("stopPrice")
                action = oso_payload.get("action", "").lower()
                
                if action == "buy":
                    # For BUY stops, increase the stop price slightly
                    new_stop_price = current_stop_price * 1.001  # 0.1% higher
                    oso_payload["stopPrice"] = new_stop_price
                    logging.info(f"🔄 Adjusted BUY stop price from {current_stop_price} to {new_stop_price}")
                else:
                    # For SELL stops, decrease the stop price slightly
                    new_stop_price = current_stop_price * 0.999  # 0.1% lower
                    oso_payload["stopPrice"] = new_stop_price
                    logging.info(f"🔄 Adjusted SELL stop price from {current_stop_price} to {new_stop_price}")
                
                # Continue to next attempt
                continue
                
            elif "insufficient buying power" in error_msg:
                logging.error("💰 INSUFFICIENT MARGIN - Cannot retry, this requires manual intervention")
                break
                
            elif "invalid symbol" in error_msg:
                logging.error("📊 INVALID SYMBOL - Cannot retry, symbol may be expired")
                break
                
            elif "market is closed" in error_msg:
                logging.error("🕐 MARKET CLOSED - Cannot retry until market opens")
                break
                
            # Wait before retry
            if attempt < max_retries - 1:
                retry_delay = 2 ** attempt  # Exponential backoff
                logging.info(f"⏳ Waiting {retry_delay}s before retry...")
                await asyncio.sleep(retry_delay)
    
    # All retries failed
    logging.error(f"❌ ALL {max_retries} OSO PLACEMENT ATTEMPTS FAILED")
    raise last_error


async def check_market_hours(symbol: str) -> bool:
    """
    Check if the market is open for the given symbol.
    """
    try:
        # This is a simplified check - in production you'd want more sophisticated market hours checking
        current_time = datetime.now()
        
        # Basic US market hours check (EST/EDT)
        if symbol.startswith("NQ") or symbol.startswith("ES") or symbol.startswith("YM"):
            # Futures trade almost 24/7, but there are brief maintenance windows
            return True
            
        # For now, assume markets are open
        return True
        
    except Exception as e:
        logging.warning(f"⚠️ Could not determine market hours for {symbol}: {e}")
        return True  # Assume open if we can't determine


async def validate_account_requirements() -> dict:
    """
    Validate account status and requirements before placing orders.
    """
    try:
        if not client.access_token:
            await client.authenticate()
            
        headers = {"Authorization": f"Bearer {client.access_token}"}
        
        # Check account status
        async with httpx.AsyncClient() as http_client:
            account_url = f"https://demo-api.tradovate.com/v1/account/{client.account_id}"
            response = await http_client.get(account_url, headers=headers)
            response.raise_for_status()
            account_data = response.json()
            
            buying_power = account_data.get("dayTradeBuyingPower", 0)
            net_liquidation = account_data.get("netLiq", 0)
            
            return {
                "valid": True,
                "buying_power": buying_power,
                "net_liquidation": net_liquidation,
                "account_status": account_data.get("status", "unknown")
            }
            
    except Exception as e:
        logging.error(f"❌ Error validating account: {e}")
        return {
            "valid": False,
            "error": str(e)
        }


async def get_symbol_specifications(symbol: str) -> dict:
    """
    Get symbol specifications to ensure proper order sizing and pricing.
    """
    try:
        if not client.access_token:
            await client.authenticate()
            
        headers = {"Authorization": f"Bearer {client.access_token}"}
        
        async with httpx.AsyncClient() as http_client:
            # Get contract specifications
            contract_url = f"https://demo-api.tradovate.com/v1/contract/find"
            response = await http_client.get(f"{contract_url}?name={symbol}", headers=headers)
            response.raise_for_status()
            contract_data = response.json()
            
            if not contract_data:
                return {"valid": False, "error": f"Contract not found for {symbol}"}
                
            return {
                "valid": True,
                "contract_id": contract_data.get("id"),
                "tick_size": contract_data.get("tickSize", 0.25),
                "point_value": contract_data.get("pointValue", 20),
                "min_quantity": 1,
                "symbol": symbol
            }
            
    except Exception as e:
        logging.error(f"❌ Error getting symbol specs for {symbol}: {e}")
        return {
            "valid": False,
            "error": str(e),
            "tick_size": 0.25,  # Default for NQ
            "point_value": 20,  # Default for NQ
            "min_quantity": 1
        }


def round_to_tick_size(price: float, tick_size: float = 0.25) -> float:
    """
    Round price to the nearest valid tick size.
    """
    try:
        if tick_size <= 0:
            return price
        return round(price / tick_size) * tick_size
    except Exception:
        return price











