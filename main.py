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

# 🚀 SPEED OPTIMIZATION: Faster logging configuration
log_file = os.path.join(LOG_DIR, "webhook_trades.log")

# Configure logging for maximum performance
file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# 🚀 SPEED: Simpler log format for faster processing
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Configure root logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.handlers.clear()  # Clear any existing handlers
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# 🚀 SPEED: Disable debug logging for httpx to reduce noise
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI()
client = TradovateClient()
# Dictionary of asyncio locks per symbol to serialize webhook handling and prevent race conditions
symbol_locks = {}

# 🚀 SPEED OPTIMIZATION: Persistent HTTP client to avoid connection overhead
persistent_http_client = None

async def get_http_client():
    """Get or create a persistent HTTP client for maximum speed"""
    global persistent_http_client
    if persistent_http_client is None:
        # Configure for maximum performance
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
        timeout = httpx.Timeout(10.0, connect=5.0)  # Faster timeouts
        persistent_http_client = httpx.AsyncClient(
            limits=limits, 
            timeout=timeout,
            http2=True  # Use HTTP/2 for better performance
        )
    return persistent_http_client

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

@app.on_event("shutdown")
async def shutdown_event():
    """🚀 SPEED: Cleanup persistent HTTP client on shutdown"""
    global persistent_http_client
    if persistent_http_client:
        await persistent_http_client.aclose()
        persistent_http_client = None
    logging.info("=== APPLICATION SHUTDOWN COMPLETE ===")

async def cancel_all_orders(symbol):
    """🚀 ULTRA-FAST order cancellation with parallel processing"""
    list_url = f"https://demo-api.tradovate.com/v1/order/list"
    cancel_url = f"https://demo-api.tradovate.com/v1/order/cancel"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    
    http_client = await get_http_client()
    
    # 🚀 SPEED: Reduced max retries and faster polling
    max_retries = 3  # Reduced from 8
    for attempt in range(max_retries):
        # Get all orders
        resp = await http_client.get(list_url, headers=headers)
        resp.raise_for_status()
        orders = resp.json()
        
        # Filter orders to cancel
        open_orders = [o for o in orders if o.get("symbol") == symbol and o.get("status") not in ("Filled", "Cancelled", "Rejected")]
        if not open_orders:
            break
            
        # 🚀 PARALLEL CANCELLATION: Cancel all orders simultaneously
        cancel_tasks = []
        for order in open_orders:
            oid = order.get("id")
            if oid:
                task = asyncio.create_task(cancel_single_order(http_client, cancel_url, oid, symbol, order.get('status')))
                cancel_tasks.append(task)
        
        # Wait for all cancellations to complete in parallel
        if cancel_tasks:
            await asyncio.gather(*cancel_tasks, return_exceptions=True)
        
        # 🚀 SPEED: Reduced sleep from 0.5 to 0.1 seconds
        await asyncio.sleep(0.1)
    
    # 🚀 SPEED: Skip final verification check for maximum speed
    # Final check removed to save time - if orders remain, they'll be handled in next cycle

async def cancel_single_order(http_client, cancel_url, order_id, symbol, status):
    """Cancel a single order - used for parallel processing"""
    try:
        await http_client.post(f"{cancel_url}/{order_id}", headers={"Authorization": f"Bearer {client.access_token}"})
        # 🚀 SPEED: Reduced logging verbosity
        logging.info(f"✅ Cancelled {order_id} ({status})")
    except Exception as e:
        logging.error(f"❌ Cancel failed {order_id}: {e}")

async def flatten_position(symbol):
    """🚀 ULTRA-FAST position flattening"""
    url = f"https://demo-api.tradovate.com/v1/position/closeposition"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    http_client = await get_http_client()
    await http_client.post(url, headers=headers, json={"symbol": symbol})


async def wait_until_no_open_orders(symbol, timeout=5):  # 🚀 SPEED: Reduced timeout from 10 to 5 seconds
    """
    🚀 OPTIMIZED: Poll Tradovate with faster intervals until no open orders remain
    """
    url = f"https://demo-api.tradovate.com/v1/order/list"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    http_client = await get_http_client()
    start = asyncio.get_event_loop().time()
    
    while True:
        resp = await http_client.get(url, headers=headers)
        resp.raise_for_status()
        orders = resp.json()
        open_orders = [o for o in orders if o.get("symbol") == symbol and o.get("status") in ("Working", "Accepted")]
        if not open_orders:
            return
        if asyncio.get_event_loop().time() - start > timeout:
            logging.warning(f"⚡ Timeout waiting for orders to clear: {symbol}")
            return
        # 🚀 SPEED: Reduced sleep from 0.5 to 0.1 seconds for faster polling
        await asyncio.sleep(0.1)

def parse_alert_to_tradovate_json(alert_text: str, account_id: int) -> dict:
    # 🚀 SPEED: Reduced logging verbosity for faster parsing
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
        elif line.strip().upper() in ["BUY", "SELL"]:
            parsed_data["action"] = line.strip().capitalize()

    # 🚀 SPEED: Single comprehensive log instead of multiple logs
    logging.info(f"⚡ Parsed: {parsed_data}")

    required_fields = ["symbol", "action"]
    for field in required_fields:
        if field not in parsed_data or not parsed_data[field]:
            raise ValueError(f"Missing or invalid field: {field}")

    # Convert numeric fields
    for target in ["T1", "STOP", "PRICE"]:
        if target in parsed_data:
            parsed_data[target] = float(parsed_data[target])

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
       
        # 🚀 SPEED OPTIMIZATION: Parallel cleanup operations for maximum speed
        logging.info("⚡ === ULTRA-FAST PARALLEL CLEANUP === ⚡")
        
        cleanup_tasks = []
        
        # Start position closing and order cancellation in parallel
        try:
            # Task 1: Close all positions
            close_task = asyncio.create_task(client.force_close_all_positions_immediately())
            cleanup_tasks.append(("close_positions", close_task))
            
            # Task 2: Cancel all pending orders (generic)
            cancel_task = asyncio.create_task(client.cancel_all_pending_orders())
            cleanup_tasks.append(("cancel_orders", cancel_task))
            
            # Task 3: Cancel symbol-specific orders
            symbol_cancel_task = asyncio.create_task(cancel_all_orders(symbol))
            cleanup_tasks.append(("cancel_symbol_orders", symbol_cancel_task))
            
            # Execute all cleanup operations in parallel
            results = await asyncio.gather(*[task for name, task in cleanup_tasks], return_exceptions=True)
            
            for i, (name, task) in enumerate(cleanup_tasks):
                result = results[i]
                if isinstance(result, Exception):
                    logging.warning(f"⚡ {name} failed: {result}")
                else:
                    logging.info(f"✅ {name} completed")
                    
        except Exception as e:
            logging.error(f"⚡ Parallel cleanup error: {e}")
            # Continue anyway - speed is priority
        
        # 🚀 SPEED: Skip final order verification wait - place order immediately
        # This saves 1-5 seconds of polling time
        
        # STEP 3: Place entry order with automatic bracket orders (OSO)
        logging.info(f"⚡ === ULTRA-FAST OSO PLACEMENT ===")
        
        # Determine opposite action for take profit and stop loss
        opposite_action = "Sell" if action.lower() == "buy" else "Buy"
        
        # 🚀 SPEED: Pre-build OSO payload for fastest execution
        oso_payload = {
            "accountSpec": client.account_spec,
            "accountId": client.account_id,
            "action": action.capitalize(),  # "Buy" or "Sell"
            "symbol": symbol,
            "orderQty": 1,
            "orderType": order_type,   # "Stop"
            "stopPrice": entry_price,  # 🚀 SPEED: Set exact price immediately
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
       
        # 🚀 SPEED: Remove redundant logging and validation for maximum speed
        logging.info(f"⚡ {symbol} {action} @ {entry_price} | TP:{t1} SL:{stop}")
        oso_payload["stopPrice"] = entry_price
        logging.info(f"🎯 STOP ENTRY at exact alert PRICE={entry_price}")
        logging.info(f"🎯 Take Profit at T1={t1}")
        logging.info(f"🎯 Stop Loss at STOP={stop}")
       
        logging.info(f"=== OSO PAYLOAD ===")
        logging.info(f"{json.dumps(oso_payload, indent=2)}")
       
        # STEP 4: Place OSO bracket order with speed optimizations
        logging.info("=== PLACING OSO BRACKET ORDER ===")
       
        # STEP 4: Place OSO bracket order with maximum speed optimizations
        try:
            # 🚀 FASTEST EXECUTION: Place OSO order immediately with timer
            start_time = time.time()
            oso_result = await client.place_oso_order(oso_payload)
            execution_time = (time.time() - start_time) * 1000  # Convert to milliseconds
           
            logging.info(f"⚡ OSO SUCCESS in {execution_time:.1f}ms: {oso_result.get('orderId', 'ID_PENDING')}")
           
            return {
                "status": "success",
                "order": oso_result,
                "execution_time_ms": execution_time,
                "order_type": order_type,
                "symbol": symbol,
                "entry_price": entry_price,
                "take_profit": t1,
                "stop_loss": stop
            }
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000 if 'start_time' in locals() else 0
            logging.error(f"⚡ OSO FAILED after {execution_time:.1f}ms: {e}")
            
            # � SPEED: Minimal error analysis for faster recovery
            error_msg = str(e).lower()
            if "price" in error_msg:
                logging.error(f"⚡ PRICE ERROR: {entry_price}")
            elif "buying power" in error_msg:
                logging.error("⚡ MARGIN ERROR")
            elif "symbol" in error_msg:
                logging.error(f"⚡ SYMBOL ERROR: {symbol}")
            
            raise HTTPException(status_code=500, detail=f"OSO failed: {str(e)}")
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
    
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
