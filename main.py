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
    while True:
        try:
            # Check the status of the SL order
            url = f"https://demo-api.tradovate.com/v1/order/{sl_order_id}"
            headers = {"Authorization": f"Bearer {client.access_token}"}
            async with httpx.AsyncClient() as http_client:
                response = await http_client.get(url, headers=headers)
                response.raise_for_status()
                order_status = response.json()

            # If the stop loss order is filled or triggered
            if order_status.get("status") == "Filled" or order_status.get("status") == "Triggered":
                logging.info(f"SL order {sl_order_id} was {order_status.get('status')}. Cancelling TP orders: {tp_order_ids}")

                # Cancel all TP orders regardless of open position status
                for tp_order_id in tp_order_ids:
                    try:
                        cancel_url = f"https://demo-api.tradovate.com/v1/order/cancel/{tp_order_id}"
                        async with httpx.AsyncClient() as http_client:
                            cancel_response = await http_client.post(cancel_url, headers=headers)
                            cancel_response.raise_for_status()
                            logging.info(f"Successfully cancelled TP order {tp_order_id}")
                    except Exception as e:
                        logging.error(f"Error cancelling TP order {tp_order_id}: {e}")
                break

            elif order_status.get("status") in ["Cancelled", "Rejected"]:
                logging.info(f"SL order {sl_order_id} was {order_status.get('status')}. Stopping monitoring.")
                break

            await asyncio.sleep(1)  # Poll every second

        except Exception as e:
            logging.error(f"Error monitoring SL order {sl_order_id}: {e}")
            await asyncio.sleep(5)  # Retry after a delay

async def place_order(symbol, action, quantity, order_data):
    """
    Custom place_order implementation to ensure proper handling of order types
    """
    url = "https://demo-api.tradovate.com/v1/order/placeorder"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    
    # Ensure we're strictly following the order_data properties for order placement
    # This prevents any automatic filling of orders at market price
    
    # Log the exact order we're sending to the API
    logging.info(f"Sending order to API: {order_data}")
    
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, headers=headers, json=order_data)
        response.raise_for_status()
        result = response.json()
        
        # Log the response we get back
        logging.info(f"API response for order: {result}")
        
        return result

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
                symbol_raw = text_data.split("symbol=")[1].split("\n")[0].strip()
                if "," in symbol_raw:
                    symbol_raw = symbol_raw.split(",")[0]
                latest_price = await get_latest_price(symbol_raw)
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

        # Cancel all previous orders and flatten positions for this symbol
        await cancel_all_orders(symbol)
        await flatten_position(symbol)

        order_plan = []
        # Only place orders if we have a specific PRICE for entry
        if "PRICE" in data:
            # Add the ENTRY limit order
            order_plan.append({
                "label": "ENTRY", 
                "action": action, 
                "orderType": "Limit", 
                "price": data["PRICE"], 
                "qty": 3
            })
            
            # Only add TP and SL orders if we have an entry point
            for i in range(1, 4):
                key = f"T{i}"
                if key in data:
                    order_plan.append({
                        "label": f"TP{i}",
                        "action": "Sell" if action.lower() == "buy" else "Buy",
                        "orderType": "Limit",
                        "price": data[key],
                        "qty": 1
                    })

            if "STOP" in data:
                order_plan.append({
                    "label": "STOP",
                    "action": "Sell" if action.lower() == "buy" else "Buy",
                    "orderType": "Stop",
                    "stopPrice": data["STOP"],
                    "qty": 3
                })
        else:
            logging.warning("No PRICE specified in the alert data. Cannot place limit order.")
            return {"status": "error", "detail": "No PRICE specified for limit order"}

        sl_order_id = None
        tp_order_ids = []

        order_results = []
        for order in order_plan:
            order_payload = {
                "accountId": client.account_id,
                "symbol": symbol,
                "action": order["action"],
                "orderQty": order["qty"],
                "orderType": order["orderType"],
                "timeInForce": "GTC",  # Good Till Cancelled
                "isAutomated": True
            }

            if order["orderType"] == "Limit":
                # Ensure we're setting a limit price for limit orders
                order_payload["price"] = order["price"]
            elif order["orderType"] == "StopLimit":
                order_payload["price"] = order["price"]
                order_payload["stopPrice"] = order["stopPrice"]
            elif order["orderType"] == "Stop":
                order_payload["stopPrice"] = order["stopPrice"]

            logging.info(f"Placing {order['label']} order: {order_payload}")
            retry_count = 0
            while retry_count < 3:
                try:
                    result = await place_order(
                        symbol=symbol,
                        action=order["action"],
                        quantity=order["qty"],
                        order_data=order_payload
                    )

                    if order["label"] == "STOP":
                        sl_order_id = result.get("id")
                    elif order["label"].startswith("TP"):
                        tp_order_ids.append(result.get("id"))

                    order_results.append({order["label"]: result})
                    break
                except Exception as e:
                    logging.error(f"Error placing {order['label']} order (attempt {retry_count + 1}): {e}")
                    retry_count += 1
                    if retry_count == 3:
                        order_results.append({order["label"]: str(e)})

        # Start monitoring the stop order in the background
        if sl_order_id and tp_order_ids:
            asyncio.create_task(monitor_stop_order_and_cancel_tp(sl_order_id, tp_order_ids))

        return {"status": "success", "order_responses": order_results}

    except Exception as e:
        logging.error(f"Unexpected error in webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
