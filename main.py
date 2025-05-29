import os
import logging
import json
import asyncio
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from tradovate_api import TradovateClient
import uvicorn
import httpx
import hashlib

# Dictionary to track last alerts to prevent duplicates
last_alert = {}  # {symbol: {"direction": "buy"/"sell", "timestamp": datetime}}

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
    await client.authenticate()


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
    alert_string = json.dumps(data, sort_keys=True)
    return hashlib.sha256(alert_string.encode()).hexdigest()

# Direct API function to place a stop loss order (used as fallback)
async def place_stop_loss_order(stop_order_data):
    """
    Place a stop loss order using direct Tradovate API calls.
    This is a standalone function for emergency fallback.
    """
    logging.info(f"DIRECT API: Placing STOP LOSS order with data: {json.dumps(stop_order_data)}")
    
    # Validate stop order data before placing
    required_fields = ["accountId", "symbol", "action", "orderQty", "orderType", "stopPrice"]
    for field in required_fields:
        if field not in stop_order_data:
            error_msg = f"Missing required field in stop_order_data: {field}"
            logging.error(error_msg)
            return None, error_msg
    
    # Place STOP LOSS order using direct API call
    place_url = "https://demo-api.tradovate.com/v1/order/placeorder"
    headers = {"Authorization": f"Bearer {client.access_token}", "Content-Type": "application/json"}
    
    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(
                place_url,
                headers=headers,
                json=stop_order_data
            )
            response.raise_for_status()
            stop_result = response.json()
            
            if "errorText" in stop_result:
                error_msg = f"STOP LOSS order placement failed with API error: {stop_result['errorText']}"
                logging.error(error_msg)
                return None, error_msg
            else:
                stop_id = stop_result.get("id")
                if not stop_id:
                    error_msg = f"STOP LOSS order placement returned no order ID: {stop_result}"
                    logging.error(error_msg)
                    return None, error_msg
                else:
                    logging.info(f"DIRECT API: STOP LOSS order placed successfully with ID {stop_id}")
                    logging.info(f"Full STOP LOSS order response: {stop_result}")
                    return stop_id, None
    except Exception as e:
        error_msg = f"Error placing STOP LOSS order: {e}"
        logging.error(error_msg)
        return None, error_msg

# CRITICAL: Fixed monitor_all_orders function to properly handle stop loss placement
async def monitor_all_orders(order_tracking, symbol, stop_order_data=None):
    """
    Monitor all orders and manage their relationships:
    - If ENTRY is filled, place the STOP order and keep TP active
    - If TP is filled, cancel STOP
    - If STOP is filled, cancel TP
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
                if label == "ENTRY" and not entry_filled:
                    entry_filled = True

                    # Place the bracket orders (stop loss and take profit) after ENTRY is filled
                    if stop_order_data:
                        try:
                            async with httpx.AsyncClient() as http_client:
                                response = await http_client.post(
                                    f"https://demo-api.tradovate.com/v1/order/placeOSO",
                                    headers={"Authorization": f"Bearer {client.access_token}", "Content-Type": "application/json"},
                                    json=stop_order_data
                                )
                                response.raise_for_status()
                                oso_result = response.json()

                                if "orderId" in oso_result:
                                    logging.info(f"Bracket orders placed successfully: {oso_result}")
                                    stop_placed = True
                                else:
                                    logging.error(f"Failed to place bracket orders: {oso_result}")
                        except Exception as e:
                            logging.error(f"Error placing bracket orders: {e}")
                    else:
                        logging.error("Missing stop_order_data for bracket order placement.")

                # Restore logic for handling TP1 and STOP fills
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
                                    logging.warning(f"Failed to cancel STOP order after TP1 fill. Status: {resp.status_code}")
                        except Exception as e:
                            logging.error(f"Exception while cancelling STOP order after TP1 fill: {e}")

                elif label == "STOP" and entry_filled:
                    logging.info(f"STOP order filled! Cancelling TP1 order.")
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

        except Exception as e:
            logging.error(f"Error in monitor_all_orders: {e}")

        # Break the loop if monitoring time exceeds the maximum limit
        if asyncio.get_event_loop().time() - monitoring_start_time > max_monitoring_time:
            logging.warning(f"Monitoring timeout reached for {symbol}.")
            break
        
        await asyncio.sleep(poll_interval)
