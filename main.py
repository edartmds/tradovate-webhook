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
        response = await http_client.post(url, headers=headers, json={"symbol": symbol})
        response.raise_for_status()
        logging.info(f"Cancelled all orders for {symbol}: {response.status_code}")

async def flatten_position(symbol):
    url = f"https://demo-api.tradovate.com/v1/position/closeposition"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(url, headers=headers, json={"symbol": symbol})
            response.raise_for_status()
            logging.info(f"Flattened position for {symbol}: {response.status_code}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # No position found, this is fine
                logging.info(f"No open position found for {symbol} to flatten")
            else:
                # Re-raise for other errors
                raise

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

async def monitor_tp_orders_and_adjust_sl(sl_order_id, tp_order_ids, total_contracts=3):
    """
    Monitor take profit orders and adjust the stop loss quantity when TPs are hit.
    
    Args:
        sl_order_id: The ID of the stop loss order to adjust
        tp_order_ids: List of take profit order IDs to monitor
        total_contracts: Total number of contracts initially placed
    """
    logging.info(f"Starting to monitor TP orders {tp_order_ids} to adjust SL order {sl_order_id}")
    
    # Track which TP orders have been filled
    filled_tp_orders = set()
    remaining_contracts = total_contracts
    
    while True:
        try:
            # Sleep briefly to avoid hammering the API
            await asyncio.sleep(1)
            
            # Get the latest state of the stop loss order
            sl_url = f"https://demo-api.tradovate.com/v1/order/{sl_order_id}"
            headers = {"Authorization": f"Bearer {client.access_token}"}
            
            # Check if SL order still exists and is active
            async with httpx.AsyncClient() as http_client:
                sl_response = await http_client.get(sl_url, headers=headers)
                sl_response.raise_for_status()
                sl_status = sl_response.json()
            
            # If stop loss is no longer active, stop monitoring
            if sl_status.get("status") in ["Filled", "Cancelled", "Rejected"]:
                logging.info(f"SL order {sl_order_id} is {sl_status.get('status')}. Stopping TP monitoring.")
                break
                
            # Check each TP order that hasn't been filled yet
            for tp_id in tp_order_ids:
                if tp_id in filled_tp_orders:
                    continue
                    
                tp_url = f"https://demo-api.tradovate.com/v1/order/{tp_id}"
                async with httpx.AsyncClient() as http_client:
                    tp_response = await http_client.get(tp_url, headers=headers)
                    
                    # Skip if order doesn't exist anymore
                    if tp_response.status_code == 404:
                        continue
                        
                    tp_response.raise_for_status()
                    tp_status = tp_response.json()
                
                # If a TP order has been filled
                if tp_status.get("status") == "Filled":
                    filled_tp_orders.add(tp_id)
                    logging.info(f"TP order {tp_id} has been filled!")
                    
                    # Calculate the number of contracts filled by this TP
                    contracts_filled = tp_status.get("orderQty", 1)
                    remaining_contracts -= contracts_filled
                    
                    # If we still have open positions, update the SL order quantity
                    if remaining_contracts > 0:
                        logging.info(f"Adjusting SL order {sl_order_id} to {remaining_contracts} contracts")
                        
                        # Update the stop loss order with new quantity
                        modify_url = f"https://demo-api.tradovate.com/v1/order/modifyorder"
                        modify_data = {
                            "orderId": sl_order_id,
                            "orderQty": remaining_contracts
                        }
                        
                        async with httpx.AsyncClient() as http_client:
                            modify_response = await http_client.post(modify_url, headers=headers, json=modify_data)
                            modify_response.raise_for_status()
                            logging.info(f"Successfully adjusted SL order to {remaining_contracts} contracts")
                    else:
                        # All contracts are filled, so we can stop monitoring
                        logging.info("All contracts have been filled by TP orders. Stopping monitoring.")
                        return
            
            # If all TP orders have been filled, stop monitoring
            if len(filled_tp_orders) == len(tp_order_ids):
                logging.info("All TP orders have been processed. Stopping monitoring.")
                break
                
        except Exception as e:
            logging.error(f"Error monitoring TP orders: {e}", exc_info=True)
            await asyncio.sleep(5)  # Retry after a longer delay on error

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

        # IMPORTANT: First cancel all existing orders and flatten positions
        logging.info(f"Cancelling all existing orders for {symbol}")
        await cancel_all_orders(symbol)
        
        logging.info(f"Flattening any existing positions for {symbol}")
        await flatten_position(symbol)
        
        # Wait a moment to ensure cancellations are processed
        await asyncio.sleep(1)

        order_plan = []
        # Only place new orders if we have a specific PRICE for entry
        if "PRICE" in data:
            logging.info(f"Planning new limit order at price: {data['PRICE']}")
            # Add the ENTRY limit order
            order_plan.append({
                "label": "ENTRY", 
                "action": action, 
                "orderType": "Limit", 
                "price": float(data["PRICE"]),  # Ensure this is a float
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
                        "price": float(data[key]),  # Ensure this is a float
                        "qty": 1
                    })

            if "STOP" in data:
                order_plan.append({
                    "label": "STOP",
                    "action": "Sell" if action.lower() == "buy" else "Buy",
                    "orderType": "Stop",
                    "stopPrice": float(data["STOP"]),  # Ensure this is a float
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
                    # Use our custom place_order function to ensure proper order handling
                    result = await place_order(
                        symbol=symbol,
                        action=order["action"],
                        quantity=order["qty"],
                        order_data=order_payload
                    )
                    
                    # Check if the order was created as a limit order or if it was filled immediately
                    # which would indicate a market order
                    if result.get("status") == "Filled":
                        logging.warning(f"Order {order['label']} was filled immediately, which indicates it might have been placed as a market order!")
                    
                    if order["label"] == "STOP":
                        sl_order_id = result.get("id")
                        logging.info(f"Stored SL order ID: {sl_order_id}")
                    elif order["label"].startswith("TP"):
                        tp_order_ids.append(result.get("id"))
                        logging.info(f"Added TP order ID: {result.get('id')}")

                    order_results.append({order["label"]: result})
                    break
                except Exception as e:
                    logging.error(f"Error placing {order['label']} order (attempt {retry_count + 1}): {e}")
                    retry_count += 1
                    if retry_count == 3:
                        order_results.append({order["label"]: str(e)})
                        
        # Log our collected order IDs for monitoring
        logging.info(f"SL order ID for monitoring: {sl_order_id}")
        logging.info(f"TP order IDs for potential cancellation: {tp_order_ids}")        # Start monitoring the stop order in the background if we have both SL and TP orders
        if sl_order_id and tp_order_ids:
            logging.info(f"Starting background monitoring for SL order {sl_order_id}")
            monitoring_task = asyncio.create_task(monitor_stop_order_and_cancel_tp(sl_order_id, tp_order_ids))
            logging.info(f"Starting background monitoring for TP orders to adjust SL order {sl_order_id}")
            tp_monitoring_task = asyncio.create_task(monitor_tp_orders_and_adjust_sl(sl_order_id, tp_order_ids))
            logging.info(f"Starting background monitoring for TP orders to adjust SL order {sl_order_id}")
            tp_monitoring_task = asyncio.create_task(monitor_tp_orders_and_adjust_sl(sl_order_id, tp_order_ids))

        return {"status": "success", "order_responses": order_results}

    except Exception as e:
        logging.error(f"Unexpected error in webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.on_event("startup")
async def on_startup():
    logging.info("Webhook service starting up")
    logging.info("Authenticating with Tradovate API")
    await client.authenticate()
    logging.info("Authentication successful")
    
    # Print startup message with configuration details
    logging.info("Webhook service is running with the following configuration:")
    logging.info(f"- LOG_DIR: {LOG_DIR}")
    logging.info(f"- MAX_HASHES: {MAX_HASHES}")
    logging.info("Ready to receive TradingView alerts")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    logging.info(f"Starting server on port {port}")
    uvicorn.run("main:app", host="0.0.0.0", port=port)
