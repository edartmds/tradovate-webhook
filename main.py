import os
from datetime import timedelta, datetime
import logging
from fastapi import FastAPI, Request, HTTPException
from tradovate_api import TradovateClient
import uvicorn
import httpx
import json
import hashlib
import asyncio

# Global variables
last_alert = {}  # {symbol: {"direction": "buy"/"sell", "timestamp": datetime}}
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
logging.info(f"Loaded WEBHOOK_SECRET: {WEBHOOK_SECRET}")

# Setup logging
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

# FastAPI setup
app = FastAPI()
client = TradovateClient()
recent_alert_hashes = set()
MAX_HASHES = 20  # Keep the last 20 unique alerts

# Global processing lock to ensure only one alert processes at a time
processing_lock = asyncio.Lock()
currently_processing_symbol = None

@app.on_event("startup")
async def startup_event():
    await client.authenticate()

async def get_latest_price(symbol: str):
    url = f"https://demo-api.tradovate.com/v1/marketdata/quote/{symbol}"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data["last"]
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logging.warning(f"Market data not found for symbol {symbol} (404). Proceeding with fallback logic.")
                raise ValueError(f"Market data not found for symbol {symbol}")
            else:
                logging.error(f"HTTP error getting market data for {symbol}: {e}")
                raise
        except Exception as e:
            logging.error(f"Unexpected error getting market data for {symbol}: {e}")
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
    cancel_url = f"https://demo-api.tradovate.com/v1/order/cancel"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    start = asyncio.get_event_loop().time()
    while True:
        async with httpx.AsyncClient() as http_client:
            resp = await http_client.get(url, headers=headers)
            resp.raise_for_status()
            orders = resp.json()
            # Cancel any open orders for this symbol that are not Filled/Cancelled/Rejected
            open_orders = [o for o in orders if o.get("symbol") == symbol and o.get("status") not in ("Filled", "Cancelled", "Rejected")]
            for order in open_orders:
                oid = order.get("id")
                if oid:
                    try:
                        await http_client.post(f"{cancel_url}/{oid}", headers=headers)
                        logging.info(f"wait_until_no_open_orders: Cancelled lingering order {oid} for {symbol} (status: {order.get('status')})")
                    except Exception as e:
                        logging.error(f"wait_until_no_open_orders: Failed to cancel lingering order {oid} for {symbol}: {e}")
            # After cancel attempts, check if any remain
            resp2 = await http_client.get(url, headers=headers)
            resp2.raise_for_status()
            orders2 = resp2.json()
            still_open = [o for o in orders2 if o.get("symbol") == symbol and o.get("status") not in ("Filled", "Cancelled", "Rejected")]
            if not still_open:
                return
        if asyncio.get_event_loop().time() - start > timeout:
            logging.warning(f"Timeout waiting for all open orders to clear for {symbol}.")
            return
        await asyncio.sleep(0.5)

def parse_alert_to_tradovate_json(alert_text: str, account_id: int, latest_price: float = None) -> dict:
    logging.info(f"Raw alert text: {alert_text}")
    try:
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

    except Exception as e:
        logging.error(f"Error parsing alert: {e}")
        raise ValueError(f"Error parsing alert: {e}")

def hash_alert(data: dict) -> str:
    alert_string = json.dumps(data, sort_keys=True)
    return hashlib.sha256(alert_string.encode()).hexdigest()

async def monitor_all_orders(order_tracking, symbol, stop_order_data=None):
    """
    Monitor all orders and manage their relationships:
    - If ENTRY is filled, place the STOP order and keep TP active
    - If TP is filled, cancel STOP
    - If STOP is filled, cancel TP
    """
    logging.info(f"Starting comprehensive order monitoring for {symbol}")
    entry_filled = False
    monitoring_start_time = asyncio.get_event_loop().time()
    max_monitoring_time = 3600  # 1 hour timeout
    
    while True:
        try:
            headers = {"Authorization": f"Bearer {client.access_token}"}
            active_orders = {}
            logging.info(f"Order tracking state: {order_tracking}")
            # Check status of all tracked orders
            for label, order_id in order_tracking.items():
                if order_id is None:
                    logging.info(f"Order {label} is not yet placed (order_id=None)")
                    continue
                url = f"https://demo-api.tradovate.com/v1/order/{order_id}"
                async with httpx.AsyncClient() as http_client:
                    response = await http_client.get(url, headers=headers)
                    response.raise_for_status()
                    order_status = response.json()
                status = order_status.get("status")
                logging.info(f"Order {label} (ID: {order_id}) status: {status} | Full order_status: {order_status}")
                
                if status == "Filled":
                    if label == "ENTRY" and not entry_filled:
                        logging.info(f"ENTRY order filled! Now placing STOP order for protection.")
                        entry_filled = True
                        logging.info(f"STOP order data to be used: {stop_order_data}")
                        # Now place the STOP order since we're in position
                        if stop_order_data:
                            try:
                                # Validate stop order data before placing
                                required_fields = ["accountId", "symbol", "action", "orderQty", "orderType", "stopPrice"]
                                for field in required_fields:
                                    if field not in stop_order_data:
                                        raise ValueError(f"Missing required field in stop_order_data: {field}")
                                logging.info(f"Placing STOP order with data: {stop_order_data}")
                                stop_result = await client.place_order(
                                    symbol=symbol,
                                    action=stop_order_data["action"],
                                    quantity=stop_order_data["orderQty"],
                                    order_data=stop_order_data
                                )
                                logging.info(f"STOP order placement API response: {stop_result}")
                                order_tracking["STOP"] = stop_result.get("id")
                                logging.info(f"STOP order placed successfully: {stop_result}")
                            except Exception as e:
                                logging.error(f"Error placing STOP order after ENTRY fill: {e}")
                                # Try alternative approach if first attempt fails
                                try:
                                    logging.info("Attempting fallback method for STOP order placement")
                                    headers = {"Authorization": f"Bearer {client.access_token}"}
                                    async with httpx.AsyncClient() as http_client:
                                        response = await http_client.post(
                                            f"https://demo-api.tradovate.com/v1/order/placeorder",
                                            headers=headers,
                                            json=stop_order_data
                                        )
                                        response.raise_for_status()
                                        stop_result = response.json()
                                        logging.info(f"STOP order placement fallback API response: {stop_result}")
                                        order_tracking["STOP"] = stop_result.get("id")
                                        logging.info(f"STOP order placed successfully (fallback): {stop_result}")
                                except Exception as e2:
                                    logging.error(f"Failed to place STOP order with fallback method: {e2}")
                                    logging.error(f"Position will remain UNPROTECTED by stop loss!")
                        else:
                            logging.warning("No stop_order_data provided - position will remain UNPROTECTED!")
                        
                    elif label == "TP1" and entry_filled:
                        logging.info(f"TP1 order filled! Cancelling STOP order.")
                        if order_tracking.get("STOP"):
                            cancel_url = f"https://demo-api.tradovate.com/v1/order/cancel/{order_tracking['STOP']}"
                            try:
                                async with httpx.AsyncClient() as http_client:
                                    resp = await http_client.post(cancel_url, headers=headers)
                                    if resp.status_code == 200:
                                        logging.info(f"STOP order {order_tracking['STOP']} cancelled after TP1 fill.")
                                    else:
                                        logging.warning(f"Failed to cancel STOP order {order_tracking['STOP']} after TP1 fill. Status: {resp.status_code}")
                            except Exception as e:
                                logging.error(f"Exception while cancelling STOP order after TP1 fill: {e}")
                        else:
                            logging.info("No STOP order to cancel after TP1 fill.")
                        return  # Exit monitoring
                        
                    elif label == "STOP" and entry_filled:
                        logging.info(f"STOP order filled! Cancelling TP orders.")
                        if order_tracking.get("TP1"):
                            cancel_url = f"https://demo-api.tradovate.com/v1/order/cancel/{order_tracking['TP1']}"
                            async with httpx.AsyncClient() as http_client:
                                await http_client.post(cancel_url, headers=headers)
                        return  # Exit monitoring
                        
                elif status in ["Working", "Accepted"]:
                    active_orders[label] = order_id
                elif status in ["Cancelled", "Rejected"]:
                    logging.info(f"Order {label} was {status}")
                    
            # Check if monitoring has been running too long
            if asyncio.get_event_loop().time() - monitoring_start_time > max_monitoring_time:
                logging.warning(f"Order monitoring timeout reached for {symbol}. Stopping monitoring.")
                return
                
            # If no active orders remain, stop monitoring
            if not active_orders:
                logging.info("No active orders remaining. Stopping monitoring.")
                return
                
            await asyncio.sleep(1)
            
        except Exception as e:
            logging.error(f"Error in comprehensive order monitoring: {e}")
            await asyncio.sleep(5)

# Ensure deduplication logic is robust
@app.post("/webhook")
async def webhook(req: Request):
    global recent_alert_hashes
    global last_alert
    global currently_processing_symbol
    
    # Use global lock to ensure only one alert processes at a time
    async with processing_lock:
        logging.info("Webhook endpoint hit - acquired processing lock.")
        try:
            content_type = req.headers.get("content-type")
            raw_body = await req.body()

            latest_price = None

            if content_type == "application/json":
                data = await req.json()
            elif content_type.startswith("text/plain"):
                text_data = raw_body.decode("utf-8")
                if "symbol=" in text_data:
                    latest_price = await get_latest_price(text_data.split("symbol=")[1].split(",")[0])
                data = parse_alert_to_tradovate_json(text_data, client.account_id, latest_price)
            else:
                raise HTTPException(status_code=400, detail="Unsupported content type")

            if WEBHOOK_SECRET is None:
                raise HTTPException(status_code=500, detail="Missing WEBHOOK_SECRET")

            # Deduplication logic
            current_hash = hash_alert(data)
            if current_hash in recent_alert_hashes:
                logging.warning("Duplicate alert received. Skipping execution.")
                return {"status": "duplicate", "detail": "Duplicate alert skipped."}
            recent_alert_hashes.add(current_hash)
            if len(recent_alert_hashes) > MAX_HASHES:
                recent_alert_hashes = set(list(recent_alert_hashes)[-MAX_HASHES:])

            action = data["action"].capitalize()
            symbol = data["symbol"]
            if symbol == "CME_MINI:NQ1!" or symbol == "NQ1!":
                symbol = "NQM5"

            # Check if another symbol is currently being processed
            if currently_processing_symbol is not None and currently_processing_symbol != symbol:
                logging.warning(f"Currently processing {currently_processing_symbol}, rejecting new alert for {symbol}")
                return {"status": "rejected", "detail": f"Currently processing {currently_processing_symbol}"}
                
            # Set the currently processing symbol
            currently_processing_symbol = symbol
            
            try:
                # Fuzzy deduplication: ignore new alert for same symbol+direction within 30 seconds
                dedup_window = timedelta(seconds=30)
                alert_direction = data["action"].lower()
                now = datetime.utcnow()
                last = last_alert.get(symbol)
                if last and last["direction"] == alert_direction and (now - last["timestamp"]) < dedup_window:
                    logging.warning(f"Duplicate/near-duplicate alert for {symbol} {alert_direction} within {dedup_window}. Skipping.")
                    return {"status": "duplicate", "detail": "Duplicate/near-duplicate alert skipped (time window)."}
                last_alert[symbol] = {"direction": alert_direction, "timestamp": now}

                # Always flatten all orders and positions at the beginning of each alert, regardless of direction
                logging.info(f"=== PROCESSING NEW ALERT FOR {symbol} ===")
                logging.info(f"Flattening ALL symbols to ensure clean slate...")
                
                # Flatten ALL symbols, not just the current one
                pos_url = f"https://demo-api.tradovate.com/v1/position/list"
                headers = {"Authorization": f"Bearer {client.access_token}"}
                async with httpx.AsyncClient() as http_client:
                    pos_resp = await http_client.get(pos_url, headers=headers)
                    pos_resp.raise_for_status()
                    positions = pos_resp.json()

                # Cancel ALL orders for ALL symbols
                order_url = f"https://demo-api.tradovate.com/v1/order/list"
                async with httpx.AsyncClient() as http_client:
                    order_resp = await http_client.get(order_url, headers=headers)
                    order_resp.raise_for_status()
                    all_orders = order_resp.json()
                    
                    # Cancel ALL orders regardless of symbol
                    for order in all_orders:
                        if order.get("status") not in ("Filled", "Cancelled", "Rejected"):
                            order_symbol = order.get("symbol")
                            oid = order.get("id")
                            if oid:
                                try:
                                    await http_client.post(f"https://demo-api.tradovate.com/v1/order/cancel/{oid}", headers=headers)
                                    logging.info(f"Cancelled order {oid} for {order_symbol} (status: {order.get('status')})")
                                except Exception as e:
                                    logging.error(f"Failed to cancel order {oid} for {order_symbol}: {e}")

                # Close ALL positions regardless of symbol
                for pos in positions:
                    pos_symbol = pos.get("symbol")
                    if pos_symbol and abs(pos.get("netPos", 0)) > 0:
                        try:
                            await flatten_position(pos_symbol)
                            logging.info(f"Flattened position for {pos_symbol}")
                        except Exception as e:
                            logging.error(f"Failed to flatten position for {pos_symbol}: {e}")

                # Wait for all orders to clear
                await wait_until_no_open_orders(symbol, timeout=15)
                logging.info("All orders and positions flattened successfully.")

                # Double-check - ensure completely clean state
                async with httpx.AsyncClient() as http_client:
                    # Check positions
                    pos_resp = await http_client.get(pos_url, headers=headers)
                    pos_resp.raise_for_status()
                    positions = pos_resp.json()
                    for pos in positions:
                        if abs(pos.get("netPos", 0)) > 0:
                            logging.warning(f"Position for {pos.get('symbol')} is not flat after flatten: {pos.get('netPos')}")
                    
                    # Check orders
                    order_resp = await http_client.get(order_url, headers=headers)
                    order_resp.raise_for_status()
                    orders = order_resp.json()
                    open_orders = [o for o in orders if o.get("status") in ("Working", "Accepted")]
                    if open_orders:
                        logging.warning(f"Open orders still exist after cancel: {[{'id': o.get('id'), 'symbol': o.get('symbol'), 'status': o.get('status')} for o in open_orders]}")
                        return {"status": "skipped", "detail": "Unable to achieve clean state - open orders remain."}
                
                # Initialize the order plan
                order_plan = []
                stop_order_data = None
                logging.info("Creating order plan based on alert data")

                # Add OCO order for ENTRY and STOP
                if "PRICE" in data and "STOP" in data:
                    order_plan.append({
                        "label": "ENTRY_STOP_OCO",
                        "action": action,
                        "orderType": "OCO",
                        "ocoOrders": [
                            {
                                "orderType": "Stop",
                                "stopPrice": data["PRICE"],
                                "qty": 1
                            },
                            {
                                "orderType": "Stop",
                                "stopPrice": data["STOP"],
                                "qty": 1
                            }
                        ]
                    })
                    logging.info(f"Added OCO order for ENTRY and STOP: {data['PRICE']} and {data['STOP']}")
                else:
                    logging.warning("PRICE or STOP not found in alert data - no OCO order will be placed")

                # Add limit order for T1 (Take Profit)
                if "T1" in data:
                    order_plan.append({
                        "label": "TP1",
                        "action": "Sell" if action.lower() == "buy" else "Buy",
                        "orderType": "Limit",
                        "price": data["T1"],
                        "qty": 1
                    })
                    logging.info(f"Added limit order for TP1: {data['T1']}")
                else:
                    logging.warning("T1 not found in alert data - no take profit order will be placed")

                logging.info(f"Order plan created with {len(order_plan)} orders")

                # Initialize variables for tracking orders
                order_results = []
                order_tracking = {
                    "ENTRY_STOP_OCO": None,
                    "TP1": None
                }

                try:
                    # Flatten all positions and orders
                    logging.info("Flattening all positions and orders")
                    await flatten_position(symbol)
                    await cancel_all_orders(symbol)
                except Exception as e:
                    logging.error(f"Error flattening positions or canceling orders: {e}")

                try:
                    # Execute the order plan
                    logging.info("Executing order plan")
                    for order in order_plan:
                        order_payload = {
                            "accountId": client.account_id,
                            "symbol": symbol,
                            "action": order["action"],
                            "orderQty": order["qty"],
                            "orderType": order["orderType"],
                            "price": order.get("price"),
                            "stopPrice": order.get("stopPrice"),
                            "timeInForce": "GTC",
                            "isAutomated": True
                        }

                        if order["orderType"] == "OCO":
                            order_payload["ocoOrders"] = order["ocoOrders"]

                        logging.info(f"Placing order: {order_payload}")
                        try:
                            result = await client.place_order(
                                symbol=symbol,
                                action=order["action"],
                                quantity=order["qty"],
                                order_data=order_payload
                            )
                            logging.info(f"Order placed successfully: {result}")
                            order_id = result.get("id")
                            order_tracking[order["label"]] = order_id
                            order_results.append({order["label"]: result})
                        except Exception as e:
                            logging.error(f"Error placing order {order['label']}: {e}")
                except Exception as e:
                    logging.error(f"Error executing order plan: {e}")

                logging.info("Order plan execution completed")
                return {"status": "success", "order_responses": order_results}

                # Monitor orders and cancel opposite order when one is filled
                try:
                    await monitor_all_orders(order_tracking, symbol, stop_order_data)
                except Exception as e:
                    logging.error(f"Error monitoring orders: {e}")

# ...existing code...
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
