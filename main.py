import os
from datetime import timedelta
last_alert = {}  # {symbol: {"direction": "buy"/"sell", "timestamp": datetime}}
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
                # Extra logging for debugging order status
                if label == "ENTRY":
                    logging.info(f"ENTRY order current status: {status}")
                
                if status and status.lower() == "filled":
                    if label == "ENTRY" and not entry_filled:
                        logging.info(f"ENTRY order filled! Now placing STOP LOSS order for protection.")
                        entry_filled = True
                        logging.info(f"STOP LOSS order data to be used: {stop_order_data}")
                        # Now place the STOP LOSS order since we're in position
                        if stop_order_data:
                            try:
                                # Validate stop order data before placing
                                required_fields = ["accountId", "symbol", "action", "orderQty", "orderType", "stopPrice"]
                                for field in required_fields:
                                    if field not in stop_order_data:
                                        raise ValueError(f"Missing required field in stop_order_data: {field}")
                                logging.info(f"Placing STOP LOSS order with data: {json.dumps(stop_order_data)}")
                                # Place STOP LOSS order using Tradovate API
                                headers = {"Authorization": f"Bearer {client.access_token}"}
                                async with httpx.AsyncClient() as http_client:
                                    response = await http_client.post(
                                        f"https://demo-api.tradovate.com/v1/order/placeorder",
                                        headers=headers,
                                        json=stop_order_data
                                    )
                                    response.raise_for_status()
                                    stop_result = response.json()
                                    logging.info(f"STOP LOSS order placement API response: {stop_result}")
                                    order_tracking["STOP"] = stop_result.get("id")
                                    logging.info(f"STOP LOSS order placed successfully: {stop_result}")
                            except Exception as e:
                                logging.error(f"Error placing STOP LOSS order after ENTRY fill: {e}")
                                logging.error(f"STOP LOSS order data: {json.dumps(stop_order_data)}")
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


# Webhook endpoint (cleaned up, no deduplication, no market data fallback, no legacy logic)


# Webhook endpoint (now ignores repeated same-direction alerts until direction changes)
@app.post("/webhook")
async def webhook(req: Request):
    logging.info("Webhook endpoint hit.")
    try:
        global last_alert
        content_type = req.headers.get("content-type")
        raw_body = await req.body()

        if content_type == "application/json":
            data = await req.json()
        elif content_type.startswith("text/plain"):
            text_data = raw_body.decode("utf-8")
            data = parse_alert_to_tradovate_json(text_data, client.account_id)
        else:
            raise HTTPException(status_code=400, detail="Unsupported content type")

        if WEBHOOK_SECRET is None:
            raise HTTPException(status_code=500, detail="Missing WEBHOOK_SECRET")

        # Extract symbol and action from the alert data
        symbol = data.get("symbol")
        action = data.get("action")
        if not symbol or not action:
            raise HTTPException(status_code=400, detail="Missing required fields: symbol or action")

        # Map TradingView symbol to Tradovate symbol for all API calls
        if symbol == "CME_MINI:NQ1!" or symbol == "NQ1!":
            symbol = "NQM5"

        # Only process alert if direction has changed for this symbol
        last = last_alert.get(symbol)
        if last and last.get("direction", "").lower() == action.lower():
            logging.info(f"Ignoring repeated same-direction alert for {symbol} ({action}). Last direction: {last.get('direction')}.")
            return {"status": "ignored", "reason": "Same direction as last alert"}
        # Update last_alert for this symbol
        last_alert[symbol] = {"direction": action, "timestamp": datetime.utcnow()}

        # Reset order tracking for the new alert
        order_tracking = {
            "ENTRY": None,
            "TP1": None,
            "STOP": None
        }

        # Always flatten and cancel before placing new orders
        logging.info(f"Checking and modifying open orders for {symbol} to match the latest alert values.")
        pos_url = f"https://demo-api.tradovate.com/v1/position/list"
        order_url = f"https://demo-api.tradovate.com/v1/order/list"
        headers = {"Authorization": f"Bearer {client.access_token}"}
        async with httpx.AsyncClient() as http_client:
            # Flatten position if needed
            pos_resp = await http_client.get(pos_url, headers=headers)
            pos_resp.raise_for_status()
            positions = pos_resp.json()
            for pos in positions:
                if pos.get("symbol") == symbol and abs(pos.get("netPos", 0)) > 0:
                    logging.info(f"Flattening open position for {symbol} before modifying orders.")
                    await flatten_position(symbol)
                    break

            # Cancel all open orders for the symbol before placing new ones
            await cancel_all_orders(symbol)
            await wait_until_no_open_orders(symbol, timeout=10)
            logging.info("All open orders cancelled, proceeding to place new ENTRY and TP orders.")

        # Build OSO+OCO payload for Tradovate
        logging.info("Building OSO+OCO payload for Tradovate")
        if "PRICE" not in data or "T1" not in data or "STOP" not in data:
            raise HTTPException(status_code=400, detail="Missing required order prices (PRICE, T1, STOP)")

        entry_action = action.capitalize()
        exit_action = "Sell" if entry_action.lower() == "buy" else "Buy"
        entry_price = float(data["PRICE"])
        tp_price = float(data["T1"])
        stop_price = float(data["STOP"])

        oso_payload = {
            "accountId": client.account_id,
            "orders": [
                {
                    "action": entry_action,
                    "symbol": symbol,
                    "orderQty": 1,
                    "orderType": "Stop",
                    "stopPrice": entry_price,
                    "timeInForce": "GTC",
                    "isAutomated": True
                }
            ],
            "osoOrders": [
                {
                    "oco": [
                        {
                            "action": exit_action,
                            "symbol": symbol,
                            "orderQty": 1,
                            "orderType": "Limit",
                            "price": tp_price,
                            "timeInForce": "GTC",
                            "isAutomated": True
                        },
                        {
                            "action": exit_action,
                            "symbol": symbol,
                            "orderQty": 1,
                            "orderType": "Stop",
                            "stopPrice": stop_price,
                            "timeInForce": "GTC",
                            "isAutomated": True
                        }
                    ]
                }
            ]
        }
        logging.info(f"OSO+OCO payload: {json.dumps(oso_payload)}")

        # Place the OSO order (which will trigger OCO bracket after entry is filled)
        try:
            headers = {"Authorization": f"Bearer {client.access_token}"}
            async with httpx.AsyncClient() as http_client:
                response = await http_client.post(
                    "https://demo-api.tradovate.com/v1/order/placeoso",
                    headers=headers,
                    json=oso_payload
                )
                response.raise_for_status()
                result = response.json()
                logging.info(f"OSO+OCO order placed successfully: {result}")
            return {"status": "success", "order_response": result}
        except Exception as e:
            logging.error(f"Error placing OSO+OCO order: {e}")
            raise HTTPException(status_code=500, detail="Failed to place OSO+OCO order")
    except Exception as e:
        logging.error(f"Unexpected error in webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
