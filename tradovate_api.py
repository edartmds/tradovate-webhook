# EXACT COPY OF YOUR WORKING DEMO SCRIPT - ONLY API ENDPOINTS CHANGED TO LIVE
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


# RELAXED DUPLICATE DETECTION FOR AUTOMATED TRADING
last_alert = {}  # {symbol: {"direction": "buy"/"sell", "timestamp": datetime, "alert_hash": str}}
completed_trades = {}  # {symbol: {"last_completed_direction": "buy"/"sell", "completion_time": datetime}}
active_orders = []  # Track active order IDs to manage cancellation
DUPLICATE_THRESHOLD_SECONDS = 30  # 30 seconds - only prevent rapid-fire identical alerts
COMPLETED_TRADE_COOLDOWN = 30  # 30 seconds - minimal cooldown for automated trading


WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
logging.info(f"Loaded WEBHOOK_SECRET: {WEBHOOK_SECRET}")


LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)


# SPEED OPTIMIZATION: Faster logging configuration
log_file = os.path.join(LOG_DIR, "webhook_trades.log")


# Configure logging for maximum performance
file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.INFO)


console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)


# SPEED: Simpler log format for faster processing
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)


# Configure root logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.handlers.clear()  # Clear any existing handlers
logger.addHandler(file_handler)
logger.addHandler(console_handler)


# SPEED: Disable debug logging for httpx to reduce noise
logging.getLogger("httpx").setLevel(logging.WARNING)


app = FastAPI()
client = TradovateClient()
# Dictionary of asyncio locks per symbol to serialize webhook handling and prevent race conditions
symbol_locks = {}


# SPEED OPTIMIZATION: Persistent HTTP client to avoid connection overhead
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
    logging.info("*** FORCED LIVE TRADING MODE ***")
    logging.info("*** CONNECTING TO LIVE API ENDPOINTS ***")
    try:
        await client.authenticate()
        logging.info(f"=== LIVE AUTHENTICATION SUCCESSFUL ===")
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
    """SPEED: Cleanup persistent HTTP client on shutdown"""
    global persistent_http_client
    if persistent_http_client:
        await persistent_http_client.aclose()
        persistent_http_client = None
    logging.info("=== APPLICATION SHUTDOWN COMPLETE ===")


async def cancel_all_orders(symbol):
    """ULTRA-FAST order cancellation with parallel processing"""
    list_url = f"https://live-api.tradovate.com/v1/order/list"  # LIVE API
    cancel_url = f"https://live-api.tradovate.com/v1/order/cancel"  # LIVE API
    headers = {"Authorization": f"Bearer {client.access_token}"}
   
    http_client = await get_http_client()
   
    # SPEED: Reduced max retries and faster polling
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
           
        # PARALLEL CANCELLATION: Cancel all orders simultaneously
        cancel_tasks = []
        for order in open_orders:
            oid = order.get("id")
            if oid:
                task = asyncio.create_task(cancel_single_order(http_client, cancel_url, oid, symbol, order.get('status')))
                cancel_tasks.append(task)
       
        # Wait for all cancellations to complete in parallel
        if cancel_tasks:
            await asyncio.gather(*cancel_tasks, return_exceptions=True)
       
        # SPEED: Reduced sleep from 0.5 to 0.1 seconds
        await asyncio.sleep(0.1)
   
    # SPEED: Skip final verification check for maximum speed
    # Final check removed to save time - if orders remain, they'll be handled in next cycle


async def cancel_single_order(http_client, cancel_url, order_id, symbol, status):
    """Cancel a single order - used for parallel processing"""
    try:
        await http_client.post(f"{cancel_url}/{order_id}", headers={"Authorization": f"Bearer {client.access_token}"})
        # SPEED: Reduced logging verbosity
        logging.info(f"CANCELLED {order_id} ({status})")
    except Exception as e:
        logging.error(f"Cancel failed {order_id}: {e}")


async def flatten_position(symbol):
    """ULTRA-FAST position flattening"""
    url = f"https://live-api.tradovate.com/v1/position/closeposition"  # LIVE API
    headers = {"Authorization": f"Bearer {client.access_token}"}
    http_client = await get_http_client()
    await http_client.post(url, headers=headers, json={"symbol": symbol})


async def wait_until_no_open_orders(symbol, timeout=5):  # SPEED: Reduced timeout from 10 to 5 seconds
    """
    OPTIMIZED: Poll Tradovate with faster intervals until no open orders remain
    """
    url = f"https://live-api.tradovate.com/v1/order/list"  # LIVE API
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
            logging.warning(f"Timeout waiting for orders to clear: {symbol}")
            return
        # SPEED: Reduced sleep from 0.5 to 0.1 seconds for faster polling
        await asyncio.sleep(0.1)


def parse_alert_to_tradovate_json(alert_text: str, account_id: int) -> dict:
    # SPEED: Reduced logging verbosity for faster parsing
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


    # SPEED: Single comprehensive log instead of multiple logs
    logging.info(f"Parsed: {parsed_data}")


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
    RELAXED DUPLICATE DETECTION FOR AUTOMATED TRADING
   
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
            logging.warning(f"RAPID-FIRE DUPLICATE BLOCKED: {symbol} {action}")
            logging.warning(f"Identical alert received {time_diff:.1f} seconds ago")
            return True
   
    # REMOVED: Direction-based blocking - allow all direction changes
    # REMOVED: Post-completion blocking - allow immediate new signals
    # This enables full automated trading with position flattening
   
    # Update tracking for rapid-fire detection only
    last_alert[symbol] = {
        "direction": action.lower(),
        "timestamp": current_time,
        "alert_hash": alert_hash
    }
   
    logging.info(f"ALERT ACCEPTED: {symbol} {action} - Automated trading enabled")
    return False


def mark_trade_completed(symbol: str, direction: str):
    """Mark a trade as completed to prevent immediate duplicates."""
    completed_trades[symbol] = {
        "last_completed_direction": direction.lower(),
        "completion_time": datetime.now()
    }
    logging.info(f"TRADE COMPLETION MARKED: {symbol} {direction}")
   
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


async def monitor_order_fill(order_id: int, timeout: int = 30) -> bool:
    """
    Monitors an order until it is filled or timeout is reached.
    Returns True if filled, False otherwise.
    """
    start_time = time.time()
    logging.info(f"Monitoring order {order_id} for fill status...")
    while time.time() - start_time < timeout:
        try:
            order_status = await client.get_order_status(order_id)
            status = order_status.get("ordStatus")
            logging.info(f"Order {order_id} status: {status}")

            if status == "Filled":
                logging.info(f"✅ Order {order_id} confirmed as FILLED.")
                return True
            elif status in ["Cancelled", "Rejected", "Expired"]:
                logging.warning(f"Order {order_id} is in a terminal state: {status}. Stopping monitoring.")
                return False
            
            # Wait for a short period before polling again
            await asyncio.sleep(1)

        except Exception as e:
            logging.error(f"Error while monitoring order {order_id}: {e}", exc_info=True)
            # If there's an error, stop monitoring to be safe
            return False
            
    logging.warning(f"Timeout reached while monitoring order {order_id}. Assuming not filled.")
    return False


async def handle_trade_logic(data: dict):
    """
    Main function to handle the trade logic after webhook parsing.
    This function is called within the lock to ensure serial processing.
    """
    try:
        # 1. Extract and validate data
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

        # 2. Map symbol
        if symbol == "CME_MINI:NQ1!" or symbol == "NQ1!":
            symbol = "NQZ5"
            logging.info(f"Mapped symbol to: {symbol}")

        # 3. STRATEGY REVERSAL
        original_action = action
        action = "Sell" if original_action.lower() == "buy" else "Buy"
        original_t1 = t1
        original_stop = stop
        t1 = original_stop
        stop = original_t1
        logging.info(f"STRATEGY REVERSAL: Flipped {original_action} to {action}, T1 to {t1}, STOP to {stop}")

        # 4. MINIMAL DUPLICATE DETECTION
        if is_duplicate_alert(symbol, action, data):
            logging.warning(f"RAPID-FIRE DUPLICATE BLOCKED: {symbol} {action}")
            return {
                "status": "rejected",
                "reason": "rapid_fire_duplicate",
                "message": f"Rapid-fire duplicate alert blocked for {symbol} {action}"
            }
        logging.info(f"ALERT APPROVED: {symbol} {action} - Proceeding with automated trading")

        # 5. AGGRESSIVE CLEANUP: Flatten positions and cancel orders
        logging.info("=== AGGRESSIVE CLEANUP & FLATTEN ===")
        try:
            # These operations are critical, so we run them sequentially.
            await client.force_close_all_positions_immediately()
            logging.info("force_close_all_positions_immediately completed.")
            await client.cancel_all_pending_orders()
            logging.info("cancel_all_pending_orders completed.")
        except Exception as e:
            logging.error(f"CRITICAL CLEANUP FAILED: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Critical cleanup failed: {e}")

        # 6. PLACE ENTRY ORDER
        logging.info(f"=== PLACING ENTRY ORDER: {action} {symbol} @ {price} ===")
        entry_order_payload = {
            "accountId": client.account_id,
            "accountSpec": client.account_spec,
            "action": action,
            "symbol": symbol,
            "orderQty": 1,
            "orderType": "Stop",
            "stopPrice": round(price, 2),
            "timeInForce": "Day",
            "isAutomated": True
        }
        
        entry_order_result = await client.place_order(
            symbol=symbol, 
            action=action, 
            order_data=entry_order_payload
        )
        entry_order_id = entry_order_result.get("orderId")
        if not entry_order_id:
            raise ValueError("Failed to get orderId from entry order placement.")
        logging.info(f"ENTRY ORDER PLACED: ID {entry_order_id}")

        # 7. MONITOR ENTRY ORDER FILL
        logging.info(f"=== MONITORING ENTRY ORDER {entry_order_id} FOR FILL ===")
        entry_filled = await monitor_order_fill(entry_order_id)

        if not entry_filled:
            logging.warning(f"Entry order {entry_order_id} was not filled. Cancelling and stopping.")
            try:
                await client.cancel_order(entry_order_id)
            except Exception as e:
                logging.error(f"Failed to cancel unfilled entry order {entry_order_id}: {e}")
            return {"status": "rejected", "reason": "entry_not_filled"}

        logging.info(f"✅ ENTRY ORDER {entry_order_id} FILLED!")

        # 8. PLACE OCO BRACKET ORDER (TP and SL)
        logging.info(f"=== PLACING OCO BRACKET (TP/SL) ===")
        
        opposite_action = "Sell" if action.lower() == "buy" else "Buy"

        # Correctly position TP and SL
        if action.lower() == "sell":
            # For a SELL, TP is lower, SL is higher
            tp_price = min(float(t1), float(stop))
            sl_price = max(float(t1), float(stop))
        else: # "buy"
            # For a BUY, TP is higher, SL is lower
            tp_price = max(float(t1), float(stop))
            sl_price = min(float(t1), float(stop))

        # Ensure minimum separation
        if abs(tp_price - sl_price) < 1.0:
             sl_price += 1.0 if action.lower() == "sell" else -1.0
             logging.warning(f"Adjusted SL for minimum separation: New SL is {sl_price}")

        # Define Take Profit order (without account details)
        tp_order = {
            "action": opposite_action,
            "orderQty": 1,
            "orderType": "Limit",
            "price": round(tp_price, 2),
            "timeInForce": "GTC"
        }

        # Define Stop Loss order (without account details)
        sl_order = {
            "action": opposite_action,
            "orderQty": 1,
            "orderType": "Stop",
            "stopPrice": round(sl_price, 2),
            "timeInForce": "GTC"
        }
        
        logging.info(f"OCO PAYLOAD: TP={json.dumps(tp_order)} SL={json.dumps(sl_order)}")
        
        # Pass the symbol separately to the updated place_oco_order function
        oco_result = await client.place_oco_order(symbol, tp_order, sl_order)
        logging.info(f"✅ OCO BRACKET PLACED: {oco_result}")

        return {
            "status": "success",
            "entry_order": entry_order_result,
            "oco_bracket": oco_result
        }

    except Exception as e:
        logging.error(f"!!! ERROR IN TRADE LOGIC: {e} !!!", exc_info=True)
        # Attempt to flatten everything as a final safety measure
        try:
            await client.force_close_all_positions_immediately()
            await client.cancel_all_pending_orders()
            logging.info("Emergency flatten/cancel executed due to error.")
        except Exception as cleanup_e:
            logging.error(f"EMERGENCY CLEANUP FAILED: {cleanup_e}")
        raise HTTPException(status_code=500, detail=f"Error in trade logic: {e}")

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
        
        symbol = data.get("symbol")
        if not symbol:
            raise ValueError("Symbol not found in alert data")

        # Ensure sequential handling per symbol to prevent race conditions
        lock = symbol_locks.setdefault(symbol, asyncio.Lock())
        async with lock:
            logging.info(f"Acquired lock for {symbol}")
            return await handle_trade_logic(data)

    except Exception as e:
        logging.error(f"=== TOP-LEVEL ERROR IN WEBHOOK: {e} ===", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.post("/")
async def root_post(req: Request):
    """Accept POST requests at root and handle as webhook"""
    # Process alert via webhook handler
    return await webhook(req)


@app.api_route("/", methods=["GET", "HEAD"])
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


if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
