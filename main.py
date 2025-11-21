# main reversed live
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


# 🔴 LIVE TRADING MODE - REAL MONEY TRADING ENABLED
# Set to live Tradovate account (not demo)
TRADOVATE_LIVE_MODE = True
logging.info("🔴 *** LIVE TRADING MODE ENABLED - REAL MONEY TRADES ***")


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
   
    # 🔴 LIVE TRADING SAFETY WARNINGS
    logging.info("🔴" * 50)
    logging.info("🔴 *** LIVE TRADING MODE ENABLED ***")
    logging.info("🔴 *** REAL MONEY TRADING ACTIVE ***")
    logging.info("🔴 *** ALL TRADES WILL USE REAL FUNDS ***")
    logging.info("� *** REVERSE STRATEGY: BUY signals become SELL orders ***")
    logging.info("🔄 *** REVERSE STRATEGY: SELL signals become BUY orders ***")
    logging.info("�🔴" * 50)
   
    try:
        await client.authenticate()
        logging.info(f"=== AUTHENTICATION SUCCESSFUL ===")
        logging.info(f"🔴 LIVE Account ID: {client.account_id}")
        logging.info(f"🔴 LIVE Account Spec: {client.account_spec}")
        logging.info(f"Access Token: {'***' if client.access_token else 'None'}")
       
        # Additional safety confirmation
        logging.info("🔴 *** CONFIRMED: Connected to LIVE Tradovate account ***")
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
    list_url = f"https://live-api.tradovate.com/v1/order/list"
    cancel_url = f"https://live-api.tradovate.com/v1/order/cancel"
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
    url = f"https://live-api.tradovate.com/v1/position/closeposition"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    http_client = await get_http_client()
    await http_client.post(url, headers=headers, json={"symbol": symbol})








async def wait_until_no_open_orders(symbol, timeout=5):  # 🚀 SPEED: Reduced timeout from 10 to 5 seconds
    """
    🚀 OPTIMIZED: Poll Tradovate with faster intervals until no open orders remain
    """
    url = f"https://live-api.tradovate.com/v1/order/list"
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
    🚀 ULTRA-FAST enhanced monitoring with optimized polling
    """
    logging.info(f"⚡ Starting fast order monitoring: {symbol}")
    entry_filled = False
    monitoring_start_time = asyncio.get_event_loop().time()
    max_monitoring_time = 1800  # 🚀 SPEED: Reduced from 3600 to 1800 seconds (30 min)
   
    if not stop_order_data:
        logging.error("CRITICAL: No stop_order_data provided")
   
    http_client = await get_http_client()  # 🚀 SPEED: Use persistent client
   
    while True:
        try:
            headers = {"Authorization": f"Bearer {client.access_token}"}
            active_orders = {}
           
            # 🚀 SPEED: Parallel order status checks
            status_tasks = []
            for label, order_id in order_tracking.items():
                if order_id is None:
                    continue
               
                url = f"https://live-api.tradovate.com/v1/order/{order_id}"
                task = asyncio.create_task(http_client.get(url, headers=headers))
                status_tasks.append((label, order_id, task))
           
            # Get all statuses in parallel
            for label, order_id, task in status_tasks:
                try:
                    response = await task
                    response.raise_for_status()
                    order_status = response.json()
                    status = order_status.get("status")




                    if label == "ENTRY" and status and status.lower() == "filled" and not entry_filled:
                        entry_filled = True
                        logging.info(f"⚡ ENTRY filled: {symbol}")




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
                                response = await http_client.post(
                                    f"https://live-api.tradovate.com/v1/order/placeOSO",
                                    headers={"Authorization": f"Bearer {client.access_token}", "Content-Type": "application/json"},
                                    json=oso_payload
                                )
                                response.raise_for_status()
                                oso_result = response.json()




                                if "orderId" in oso_result:
                                    logging.info(f"⚡ OSO placed: {oso_result}")
                                else:
                                    raise ValueError(f"OSO failed: {oso_result}")
                            except Exception as e:
                                logging.error(f"⚡ OSO error: {e}")




                    elif label == "STOP" and status and status.lower() == "filled":
                        logging.info(f"⚡ STOP filled: {symbol}")
                        trade_direction = stop_order_data.get("action", "unknown") if stop_order_data else "unknown"
                        mark_trade_completed(symbol, trade_direction)




                        # 🚀 SPEED: Quick cancel of TP order
                        if order_tracking.get("TP1"):
                            asyncio.create_task(
                                http_client.post(
                                    f"https://live-api.tradovate.com/v1/order/cancel/{order_tracking['TP1']}",
                                    headers=headers
                                )
                            )
                        return




                    elif label == "TP1" and status and status.lower() == "filled":
                        logging.info(f"⚡ TP1 filled: {symbol}")
                        trade_direction = stop_order_data.get("action", "unknown") if stop_order_data else "unknown"
                        mark_trade_completed(symbol, trade_direction)




                        # 🚀 SPEED: Quick cancel of STOP order
                        if order_tracking.get("STOP"):
                            asyncio.create_task(
                                http_client.post(
                                    f"https://live-api.tradovate.com/v1/order/cancel/{order_tracking['STOP']}",
                                    headers=headers
                                )
                            )
                        return




                    elif status in ["Working", "Accepted"]:
                        active_orders[label] = order_id
                       
                except Exception as e:
                    logging.error(f"⚡ Status check error {label}: {e}")




            if asyncio.get_event_loop().time() - monitoring_start_time > max_monitoring_time:
                logging.warning(f"⚡ Monitoring timeout: {symbol}")
                return




            if not active_orders:
                logging.info("⚡ No active orders - monitoring complete")
                return




            # 🚀 SPEED: Adaptive polling - faster when entry not filled, slower after
            poll_interval = 0.2 if not entry_filled else 0.5
            await asyncio.sleep(poll_interval)




        except Exception as e:
            logging.error(f"⚡ Monitoring error: {e}")
            await asyncio.sleep(1)  # 🚀 SPEED: Reduced error recovery time








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
            symbol = "NQZ5"  # Changed from NQU5 to NQZ5
            logging.info(f"Mapped symbol to: {symbol}")
           
        # 🔄 STRATEGY REVERSAL: Flip the order direction and price targets
        # If original was BUY, we'll SELL and vice versa
        original_action = action
        original_t1 = t1
        original_stop = stop
       
        # Flip the direction: Buy becomes Sell, Sell becomes Buy
        action = "Sell" if original_action.lower() == "buy" else "Buy"
       
        # Flip the targets: STOP becomes T1, T1 becomes STOP
        t1 = original_stop
        stop = original_t1
        
        # 🎯 FIX PRICE PRECISION: Round to 2 decimal places for NQ futures
        t1 = round(float(t1), 2)
        stop = round(float(stop), 2)
        price = round(float(price), 2)
       
        logging.info(f"🔄 STRATEGY REVERSAL: Flipped {original_action} to {action}")
        logging.info(f"🔄 STRATEGY REVERSAL: Flipped T1 from {original_t1} to {t1}")
        logging.info(f"🔄 STRATEGY REVERSAL: Flipped STOP from {original_stop} to {stop}")
        logging.info(f"🎯 PRICE PRECISION: Entry={price}, TP={t1}, SL={stop}")
        
        # 🎯 VALIDATION: Ensure minimum distance between entry and targets
        min_distance = 2.0  # Minimum 2 points distance for NQ futures
        
        if action.lower() == "buy":
            # For BUY orders: TP should be higher than entry, SL should be lower
            if t1 <= price + min_distance:
                t1 = price + min_distance
                logging.warning(f"🎯 ADJUSTED TP: Too close to entry, set to {t1}")
            if stop >= price - min_distance:
                stop = price - min_distance
                logging.warning(f"🎯 ADJUSTED SL: Too close to entry, set to {stop}")
        else:
            # For SELL orders: TP should be lower than entry, SL should be higher  
            if t1 >= price - min_distance:
                t1 = price - min_distance
                logging.warning(f"🎯 ADJUSTED TP: Too close to entry, set to {t1}")
            if stop <= price + min_distance:
                stop = price + min_distance
                logging.warning(f"🎯 ADJUSTED SL: Too close to entry, set to {stop}")
        
        # 🎯 ADDITIONAL STOP LOSS VALIDATION
        # Ensure stop loss is on the correct side of entry price
        if action.lower() == "buy":
            # For BUY: Stop loss must be BELOW entry price
            if stop >= price:
                stop = price - 5.0  # Force 5 points below entry
                logging.warning(f"🎯 CRITICAL FIX: BUY stop loss moved below entry to {stop}")
        else:
            # For SELL: Stop loss must be ABOVE entry price  
            if stop <= price:
                stop = price + 5.0  # Force 5 points above entry
                logging.warning(f"🎯 CRITICAL FIX: SELL stop loss moved above entry to {stop}")
        
        logging.info(f"🎯 FINAL VALIDATED PRICES: Entry={price}, TP={t1}, SL={stop}")
        logging.info(f"🎯 STOP LOSS DIRECTION CHECK: {action} order, SL at {stop}")
       
        # Ensure sequential handling per symbol to prevent race conditions
        lock = symbol_locks.setdefault(symbol, asyncio.Lock())
        logging.info(f"📌 Waiting for lock for symbol {symbol}")
        async with lock:
            logging.info(f"🔒 Acquired lock for {symbol}")
            # 🔥 MINIMAL DUPLICATE DETECTION - Only prevent rapid-fire identical alerts
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
            # Force Limit entry at the exact alert price
            order_type = "Limit"
            order_price = price
            logging.info(f"🎯 FORCE LIMIT ENTRY at exact price {order_price}")
       
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
            "orderType": order_type,   # "Limit"
            "price": order_price,  # 🚀 SPEED: Set price immediately
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
                "price": round(float(t1), 2),  # 🎯 ENSURE PROPER PRICE FORMAT
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
                "orderType": "StopMarket",  # 🎯 FIXED: Use StopMarket instead of Stop
                "stopPrice": round(float(stop), 2),  # 🎯 ENSURE PROPER PRICE FORMAT
                "timeInForce": "GTC",
                "isAutomated": True
            }
        }
       
        # 🚀 SPEED: Remove redundant logging and validation for maximum speed
        logging.info(f"⚡ {symbol} {action} @ {order_price} | TP:{t1} SL:{stop}")
        logging.info(f"🎯 STOP LOSS CONFIG: StopMarket order at {stop} for {opposite_action}")
        logging.info(f"🎯 OSO STRUCTURE: Entry={action} | TP={opposite_action} Limit | SL={opposite_action} StopMarket")
       
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
                "symbol": symbol
            }
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000 if 'start_time' in locals() else 0
            logging.error(f"⚡ OSO FAILED after {execution_time:.1f}ms: {e}")
           
            # 🚀 SPEED: Minimal error analysis for faster recovery
            error_msg = str(e).lower()
            if "price" in error_msg:
                logging.error(f"⚡ PRICE ERROR: {order_price}")
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












@app.post("/")
async def root_post(req: Request):
    """Accept POST requests at root and handle as webhook"""
    # Process alert via webhook handler
    return await webhook(req)








@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "active",
        "service": "tradovate-webhook-LIVE-REVERSE",
        "trading_mode": "🔴 LIVE TRADING - REAL MONEY",
        "strategy": "🔄 REVERSE - Opposite of signals",
        "account_id": getattr(client, 'account_id', 'Not authenticated'),
        "endpoints": {
            "webhook": "/webhook",
            "health": "/"
        },
        "message": "🔴 LIVE REVERSE TRADING WEBHOOK - Real money trades active",
        "warning": "⚠️ All trades will use real funds and execute OPPOSITE of signals"
    }




if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)












