import os
import logging
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from tradovate_api import TradovateClient
import uvicorn
import httpx
import json
import hashlib
import asyncio

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
recent_alert_hashes = set()
MAX_HASHES = 20  # Keep the last 20 unique alerts

@app.on_event("startup")
async def startup_event():
    await client.authenticate()

async def get_latest_price(symbol: str):
    url = f"https://demo-api.tradovate.com/v1/marketdata/quote/{symbol}"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    async with httpx.AsyncClient() as http_client:
        response = await http_client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data["last"]

async def cancel_all_orders(symbol):
    url = f"https://demo-api.tradovate.com/v1/order/cancelallorders"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    async with httpx.AsyncClient() as http_client:
        await http_client.post(url, headers=headers, json={"symbol": symbol})

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
            elif line.strip().upper() in ["BUY", "SELL"]:
                parsed_data["action"] = line.strip().capitalize()

        logging.info(f"Parsed alert data: {parsed_data}")

        required_fields = ["symbol", "action"]
        for field in required_fields:
            if field not in parsed_data or not parsed_data[field]:
                raise ValueError(f"Missing or invalid field: {field}")

        for target in ["T1", "T2", "T3", "STOP", "PRICE"]:
            if target in parsed_data:
                parsed_data[target] = float(parsed_data[target])

        return parsed_data

    except Exception as e:
        logging.error(f"Error parsing alert: {e}")
        raise ValueError(f"Error parsing alert: {e}")

def hash_alert(data: dict) -> str:
    alert_string = json.dumps(data, sort_keys=True)
    return hashlib.sha256(alert_string.encode()).hexdigest()

async def monitor_stop_order_and_cancel_tp(sl_order_id, tp_order_ids):
    """
    Monitor the stop loss order and cancel associated take profit orders if the stop loss is hit.
    """
    logging.info(f"Monitoring SL order {sl_order_id} for execution.")
    tp_cancelled = False
    while True:
        try:
            # Check the status of the SL order
            url = f"https://demo-api.tradovate.com/v1/order/{sl_order_id}"
            headers = {"Authorization": f"Bearer {client.access_token}"}
            async with httpx.AsyncClient() as http_client:
                response = await http_client.get(url, headers=headers)
                response.raise_for_status()
                order_status = response.json()

            if order_status.get("status") == "Filled":
                logging.info(f"SL order {sl_order_id} was filled. Cancelling TP orders: {tp_order_ids}")
                # Cancel all TP orders
                for tp_order_id in tp_order_ids:
                    cancel_url = f"https://demo-api.tradovate.com/v1/order/cancel/{tp_order_id}"
                    async with httpx.AsyncClient() as http_client:
                        await http_client.post(cancel_url, headers=headers)
                tp_cancelled = True
                break
            elif order_status.get("status") in ["Cancelled", "Rejected"]:
                logging.info(f"SL order {sl_order_id} was {order_status.get('status')}. Stopping monitoring.")
                break
            # If any TP orders are still open after SL is filled, force cancel
            if tp_cancelled:
                for tp_order_id in tp_order_ids:
                    cancel_url = f"https://demo-api.tradovate.com/v1/order/cancel/{tp_order_id}"
                    async with httpx.AsyncClient() as http_client:
                        await http_client.post(cancel_url, headers=headers)
            await asyncio.sleep(1)  # Poll every second
        except Exception as e:
            logging.error(f"Error monitoring SL order {sl_order_id}: {e}")
            await asyncio.sleep(5)  # Retry after a delay

async def monitor_tp_and_adjust_sl(tp_order_ids, sl_order_id, sl_order_qty, symbol):
    """
    Monitor TP orders. As each TP is filled, reduce the SL order size. If all TPs are filled, cancel the SL order.
    """
    remaining_qty = sl_order_qty
    filled_tp = set()
    logging.info(f"Monitoring TP orders {tp_order_ids} to adjust SL order {sl_order_id}.")
    while True:
        try:
            all_filled = True
            for idx, tp_order_id in enumerate(tp_order_ids):
                if tp_order_id in filled_tp:
                    continue
                url = f"https://demo-api.tradovate.com/v1/order/{tp_order_id}"
                headers = {"Authorization": f"Bearer {client.access_token}"}
                async with httpx.AsyncClient() as http_client:
                    response = await http_client.get(url, headers=headers)
                    response.raise_for_status()
                    order_status = response.json()
                if order_status.get("status") == "Filled":
                    filled_tp.add(tp_order_id)
                    remaining_qty -= 1
                    logging.info(f"TP order {tp_order_id} filled. Adjusting SL order {sl_order_id} to qty {remaining_qty}.")
                    # Modify SL order to new qty if contracts remain
                    if remaining_qty > 0:
                        mod_url = f"https://demo-api.tradovate.com/v1/order/modify/{sl_order_id}"
                        payload = {"orderQty": remaining_qty}
                        async with httpx.AsyncClient() as http_client:
                            await http_client.post(mod_url, headers=headers, json=payload)
                    else:
                        # All TPs filled, cancel SL
                        cancel_url = f"https://demo-api.tradovate.com/v1/order/cancel/{sl_order_id}"
                        async with httpx.AsyncClient() as http_client:
                            await http_client.post(cancel_url, headers=headers)
                        logging.info(f"All TPs filled, SL order {sl_order_id} cancelled.")
                        return
                elif order_status.get("status") not in ["Filled", "Working"]:
                    all_filled = False
            if len(filled_tp) == len(tp_order_ids):
                # All TPs filled, SL should be cancelled already
                return
            await asyncio.sleep(1)
        except Exception as e:
            logging.error(f"Error monitoring TP orders: {e}")
            await asyncio.sleep(5)

# Ensure deduplication logic is robust
@app.post("/webhook")
async def webhook(req: Request):
    global recent_alert_hashes
    logging.info("Webhook endpoint hit.")
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

        # --- Initialize variables for tracking orders
        order_results = []

        # --- Ensure all previous orders and positions are closed before processing the new alert ---
        logging.info(f"Flattening all orders and positions for symbol: {symbol}")
        await cancel_all_orders(symbol)  # Cancel all active and pending orders
        await flatten_position(symbol)  # Close any open positions
        await wait_until_no_open_orders(symbol, timeout=10)  # Ensure no open orders remain
        logging.info("All orders and positions flattened successfully.")

        # --- Process the most recent alert ---
        # Fetch existing orders to confirm cleanup
        order_url = f"https://demo-api.tradovate.com/v1/order/list"
        headers = {"Authorization": f"Bearer {client.access_token}"}
        try:
            async with httpx.AsyncClient() as http_http_client:
                order_resp = await http_http_client.get(order_url, headers=headers)
                order_resp.raise_for_status()
                orders = order_resp.json()

            # Log any remaining orders (should be none)
            remaining_orders = [o for o in orders if o.get("symbol") == symbol]
            if remaining_orders:
                logging.warning(f"Some orders were not cleared: {remaining_orders}")
            else:
                logging.info("All previous orders successfully cleared.")
        except Exception as e:
            logging.error(f"Error fetching existing orders: {e}")

        # Proceed to place new orders based on the latest alert
        # Add limit orders for T1, T2, T3
        for i in range(1, 4):
            key = f"T{i}"
            label = f"TP{i}"
            if key in data and isinstance(data[key], (int, float)) and data[key] > 0:
                order_payload = {
                    "label": label,
                    "action": "Sell" if action.lower() == "buy" else "Buy",
                    "orderType": "Limit",
                    "price": data[key],
                    "orderQty": 1,
                    "accountId": client.account_id,
                    "symbol": symbol,
                    "timeInForce": "GTC",
                    "isAutomated": True
                }
                logging.info(f"Placing limit order for {label} at price: {data[key]}.")
                try:
                    result = await client.place_order(
                        symbol=symbol,
                        action=order_payload["action"],
                        quantity=order_payload["orderQty"],
                        order_data=order_payload
                    )
                    logging.info(f"Limit order placed successfully for {label}: {result}")
                    order_results.append({label: result})
                except Exception as e:
                    logging.error(f"Error placing limit order for {label}: {e}")

        # Add stop order for entry
        if "PRICE" in data:
            order_payload = {
                "label": "ENTRY",
                "action": action,
                "orderType": "Stop",
                "stopPrice": data["PRICE"],
                "orderQty": 3,
                "accountId": client.account_id,
                "symbol": symbol,
                "timeInForce": "GTC",
                "isAutomated": True
            }
            logging.info(f"Placing stop order for entry at price: {data['PRICE']}.")
            try:
                result = await client.place_order(
                    symbol=symbol,
                    action=order_payload["action"],
                    quantity=order_payload["orderQty"],
                    order_data=order_payload
                )
                logging.info(f"Stop order for entry placed successfully: {result}")
                order_results.append({"ENTRY": result})
            except Exception as e:
                logging.error(f"Error placing stop order for entry: {e}")

        # Add stop loss order
        if "STOP" in data:
            order_payload = {
                "label": "STOP",
                "action": "Sell" if action.lower() == "buy" else "Buy",
                "orderType": "Stop",
                "stopPrice": data["STOP"],
                "orderQty": 3,
                "accountId": client.account_id,
                "symbol": symbol,
                "timeInForce": "GTC",
                "isAutomated": True
            }
            logging.info(f"Placing stop loss order at price: {data['STOP']}.")
            try:
                result = await client.place_order(
                    symbol=symbol,
                    action=order_payload["action"],
                    quantity=order_payload["orderQty"],
                    order_data=order_payload
                )
                logging.info(f"Stop loss order placed successfully: {result}")
                order_results.append({"STOP": result})
            except Exception as e:
                logging.error(f"Error placing stop loss order: {e}")
