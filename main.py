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
            logging.warning(f"Duplicate alert detected with hash: {current_hash}. Skipping execution.")
            return {"status": "duplicate", "detail": "Duplicate alert skipped."}
        recent_alert_hashes.add(current_hash)
        if len(recent_alert_hashes) > MAX_HASHES:
            recent_alert_hashes = set(list(recent_alert_hashes)[-MAX_HASHES:])

        action = data["action"].capitalize()
        symbol = data["symbol"]
        if symbol == "CME_MINI:NQ1!" or symbol == "NQ1!":
            symbol = "NQM5"

        # --- Ensure all previous orders and positions are closed before new entry ---
        await cancel_all_orders(symbol)
        await flatten_position(symbol)
        await wait_until_no_open_orders(symbol, timeout=10)
        # ---

        # Check for open position (should be flat)
        pos_url = f"https://demo-api.tradovate.com/v1/position/list"
        headers = {"Authorization": f"Bearer {client.access_token}"}
        async with httpx.AsyncClient() as http_client:
            pos_resp = await http_client.get(pos_url, headers=headers)
            pos_resp.raise_for_status()
            positions = pos_resp.json()
            for pos in positions:
                if pos.get("symbol") == symbol and abs(pos.get("netPos", 0)) > 0:
                    logging.warning(f"Position for {symbol} is not flat after flatten. Skipping order placement.")
                    return {"status": "skipped", "detail": "Position not flat after flatten."}

        # Check for open orders (should be none)
        order_url = f"https://demo-api.tradovate.com/v1/order/list"
        async with httpx.AsyncClient() as http_http_client:
            order_resp = await http_http_client.get(order_url, headers=headers)
            order_resp.raise_for_status()
            orders = order_resp.json()
            open_orders = [o for o in orders if o.get("symbol") == symbol and o.get("status") in ("Working", "Accepted")]
            if open_orders:
                logging.warning(f"Open orders for {symbol} still exist after cancel. Skipping order placement.")
                return {"status": "skipped", "detail": "Open orders still exist after cancel."}

        # Place entry, TP, and SL orders together (bracket/OCO style)
        order_plan = []
        if "PRICE" in data:
            # Always use a LIMIT order for entry at the specified price
            order_plan.append({
                "label": "ENTRY",
                "action": action,
                "orderType": "Stop",  # Replacing Limit with Stop
                "price": data["PRICE"],
                "qty": 3
            })
        for i in range(1, 4):
            key = f"T{i}"
            if key in data:
                order_plan.append({
                    "label": f"TP{i}",
                    "action": "Sell" if action.lower() == "buy" else "Buy",
                    "orderType": "Limit",  # Changed to Limit order
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
        sl_order_id = None
        tp_order_ids = []
        sl_order_qty = 0
        order_results = []
        for order in order_plan:
            order_payload = {
                "accountId": client.account_id,
                "symbol": symbol,
                "action": order["action"],
                "orderQty": order["qty"],
                "orderType": "Stop",  # Ensure all orders are Stop orders
                "timeInForce": "GTC",
                "isAutomated": True
            }
            # Explicitly set stopPrice for all orders, including ENTRY
            if "price" in order:
                order_payload["stopPrice"] = order["price"]
            elif "stopPrice" in order:
                order_payload["stopPrice"] = order["stopPrice"]
            else:
                logging.error(f"Missing stopPrice for order: {order}")
                continue  # Skip orders without a valid stopPrice

            logging.info(f"Placing {order['label']} order: {order_payload}")
            retry_count = 0
            while retry_count < 3:
                try:
                    result = await client.place_order(
                        symbol=symbol,
                        action=order["action"],
                        quantity=order["qty"],
                        order_data=order_payload
                    )
                    if order["label"] == "STOP":
                        sl_order_id = result.get("id")
                        sl_order_qty = order["qty"]
                    elif order["label"].startswith("TP") or order["label"] == "ENTRY":
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
            asyncio.create_task(monitor_tp_and_adjust_sl(tp_order_ids, sl_order_id, sl_order_qty, symbol))
        # Add detailed logging for debugging
        logging.info(f"Webhook received data: {data}")

        # Ensure deduplication logic is robust
        if current_hash in recent_alert_hashes:
            logging.warning(f"Duplicate alert detected with hash: {current_hash}. Skipping execution.")
            return {"status": "duplicate", "detail": "Duplicate alert skipped."}

        # Log the constructed order plan for debugging
        logging.info(f"Constructed order plan: {json.dumps(order_plan, indent=2)}")

        # Add error handling for order placement
        for order in order_plan:
            try:
                order_payload = {
                    "accountId": client.account_id,
                    "symbol": symbol,
                    "action": order["action"],
                    "orderQty": order["qty"],
                    "orderType": order["orderType"],
                    "timeInForce": "GTC",
                    "isAutomated": True
                }
                if "price" in order:
                    order_payload["price"] = order["price"]
                elif "stopPrice" in order:
                    order_payload["stopPrice"] = order["stopPrice"]

                logging.info(f"Placing order: {json.dumps(order_payload, indent=2)}")
                result = await client.place_order(
                    symbol=symbol,
                    action=order["action"],
                    quantity=order["qty"],
                    order_data=order_payload
                )
                logging.info(f"Order placed successfully: {json.dumps(result, indent=2)}")
            except Exception as e:
                logging.error(f"Error placing order {order}: {e}")
                continue

        return {"status": "success", "order_responses": order_results}
    except Exception as e:
        logging.error(f"Unexpected error in webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
