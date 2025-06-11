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
                if order_id is None:
                    continue
                    
                url = f"https://demo-api.tradovate.com/v1/order/{order_id}"
                async with httpx.AsyncClient() as http_client:
                    response = await http_client.get(url, headers=headers)
                    response.raise_for_status()
                    order_status = response.json()
                    
                status = order_status.get("status")
                
                # Special focus on ENTRY order status
                if label == "ENTRY":
                    logging.info(f"CRITICAL: ENTRY order (ID: {order_id}) status: {status}")
                else:
                    logging.info(f"Order {label} (ID: {order_id}) status: {status}")
                
                if status and status.lower() == "filled":
                    # CRITICAL: If ENTRY is filled and we haven't placed stop loss yet
                    if label == "ENTRY" and not entry_filled and not stop_placed:
                        logging.info(f"==========================================")
                        logging.info(f"CRITICAL: ENTRY ORDER FILLED! Placing stop loss now!")
                        logging.info(f"==========================================")
                        entry_filled = True
                        
                        # Now place the STOP LOSS order since we're in position
                        if stop_order_data:
                            # Try up to 3 times to place the stop loss
                            for attempt in range(3):
                                try:
                                    logging.info(f"ATTEMPT #{attempt+1}: Placing STOP LOSS order")
                                    
                                    # Make sure the stop price is set correctly
                                    if "stopPrice" not in stop_order_data:
                                        logging.error("CRITICAL: No stopPrice field in stop_order_data")
                                        if "price" in stop_order_data:
                                            stop_order_data["stopPrice"] = stop_order_data["price"]
                                            logging.info(f"Using price field: {stop_order_data['stopPrice']}")
                                    
                                    # Make sure order type is set to Stop
                                    stop_order_data["orderType"] = "Stop"
                                    
                                    # Try to place the stop loss order
                                    stop_result = await client.place_order(
                                        symbol=stop_order_data.get("symbol"),
                                        action=stop_order_data.get("action"),
                                        quantity=stop_order_data.get("orderQty", 1),
                                        order_data=stop_order_data
                                    )
                                    
                                    # Check if the order placement was successful
                                    if "id" in stop_result:
                                        stop_id = stop_result.get("id")
                                        order_tracking["STOP"] = stop_id
                                        stop_placed = True
                                        logging.info(f"==========================================")
                                        logging.info(f"STOP LOSS order placed successfully with ID {stop_id}")
                                        logging.info(f"==========================================")
                                        break
                                    else:
                                        logging.error(f"Stop order placement failed: {stop_result}")
                                        # Wait a moment before retrying
                                        await asyncio.sleep(1)
                                        
                                except Exception as e:
                                    logging.error(f"Error placing stop loss (attempt {attempt+1}): {e}")
                                    await asyncio.sleep(1)
                            
                            # If we still haven't placed the stop loss, try direct API call
                            if not stop_placed:
                                logging.error("All regular attempts failed, trying direct API call for stop loss")
                                try:
                                    stop_id, error = await place_stop_loss_order(stop_order_data)
                                    if stop_id:
                                        order_tracking["STOP"] = stop_id
                                        stop_placed = True
                                        logging.info(f"STOP LOSS placed via direct API with ID {stop_id}")
                                    else:
                                        logging.error(f"Direct API STOP LOSS placement failed: {error}")
                                        logging.error(f"POSITION REMAINS UNPROTECTED!")
                                except Exception as e:
                                    logging.error(f"Exception in direct API stop loss placement: {e}")
                                    logging.error("POSITION REMAINS UNPROTECTED!")
                        else:
                            logging.error("No stop_order_data available - position unprotected!")
                    
                    # If TP1 is filled, cancel the stop loss
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
                        return  # Exit monitoring
                    
                    # If stop loss is filled, cancel the take profit
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
                        return  # Exit monitoring
                        
                elif status in ["Working", "Accepted"]:
                    active_orders[label] = order_id
                    
            # Check if we've been monitoring too long
            if asyncio.get_event_loop().time() - monitoring_start_time > max_monitoring_time:
                logging.warning(f"Order monitoring timeout reached for {symbol}. Stopping.")
                return
            
            # If no active orders remain, stop monitoring
            if not active_orders:
                logging.info("No active orders remaining. Stopping monitoring.")
                return
            
            # Check 2x per second if entry has been filled
            if not entry_filled:
                poll_interval = 0.5
            else:
                poll_interval = 1
                
            await asyncio.sleep(poll_interval)
            
        except Exception as e:
            logging.error(f"Error in order monitoring: {e}")
            await asyncio.sleep(5)


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

        # Suppress repeated same-direction alerts
        direction = action.lower()
        now = datetime.utcnow()
        last = last_alert.get(symbol)
        if last is not None:
            last_direction = last.get("direction")
            if last_direction == direction:
                logging.info(f"Suppressing alert for {symbol}: same direction '{direction}' as last processed alert.")
                return {"status": "ignored", "reason": "Same direction as last processed alert"}

        # Update last_alert for this symbol
        last_alert[symbol] = {"direction": direction, "timestamp": now}

        # Reset order tracking for the new alert
        order_tracking = {
            "ENTRY": None,
            "TP1": None, 
            "STOP": None  # Will be set after ENTRY is filled
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

        # Initialize the order plan
        order_plan = []
        stop_order_data = None
        logging.info("Creating order plan based on alert data")

        # Add limit order for T1 (Take Profit) at the exact alert value
        if "T1" in data:
            tp_price = float(data["T1"])
            order_plan.append({
                "label": "TP1",
                "action": "Sell" if action.lower() == "buy" else "Buy",
                "orderType": "Limit",
                "price": tp_price,
                "qty": 1
            })
            logging.info(f"Added limit order for TP1 at exact alert price: {tp_price}")
        else:
            logging.warning("T1 not found in alert data - no take profit order will be placed")

        # Add stop order for entry, always use stop order at the exact alert value
        if "PRICE" in data:
            entry_price = float(data["PRICE"])
            order_plan.append({
                "label": "ENTRY",
                "action": action,
                "orderType": "Stop",
                "stopPrice": entry_price,
                "qty": 1
            })
            logging.info(f"Added stop order for entry at exact alert price: {entry_price}")
        else:
            logging.warning("PRICE not found in alert data - no entry order will be placed")

        # Prepare stop loss order data (to be placed after ENTRY is filled)
        if "STOP" in data:
            stop_price = float(data["STOP"])
            # IMPORTANT: Preparing the stop loss order but NOT placing it yet
            stop_order_data = {
                "accountId": client.account_id,
                "symbol": symbol,
                "action": "Sell" if action.lower() == "buy" else "Buy",
                "orderQty": 1,
                "orderType": "Stop",
                "stopPrice": stop_price,
                "timeInForce": "GTC",
                "isAutomated": True
            }
            logging.info(f"Prepared STOP LOSS order data for after ENTRY fill at exact alert price: {stop_price}")
            # Extra logging to confirm the stop loss order is prepared but not placed
            logging.info("STOP LOSS order will be placed ONLY after ENTRY order is filled")
        else:
            logging.warning("STOP not found in alert data - no stop loss order will be placed")

        # Initialize variables for tracking orders
        order_results = []
        # NOTE: We intentionally initialize this AFTER preparing stop_order_data
        order_tracking = {
            "ENTRY": None,
            "TP1": None, 
            "STOP": None  # Will be set after ENTRY is filled
        }

        # Execute the order plan (no ENTRY fallback, no market data logic)
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

            logging.info(f"Constructed order payload: {order_payload}")

            try:
                result = await client.place_order(
                    symbol=symbol,
                    action=order["action"],
                    quantity=order["qty"],
                    order_data=order_payload
                )
                logging.info(f"Order placed successfully: {result}")
                if order['label'] == 'ENTRY':
                    logging.info(f"CRITICAL: ENTRY order API response: {result}")
                order_id = result.get("id")
                order_tracking[order["label"]] = order_id
                order_results.append({order["label"]: result})
            except Exception as e:
                logging.error(f"Error placing order {order['label']}: {e}")
                logging.error(f"Failed order payload: {order_payload}")

        # Monitor orders: place STOP LOSS after ENTRY is filled, cancel opposite when one is filled
        try:
            # This function will monitor orders and place the stop loss when entry is filled
            await monitor_all_orders(order_tracking, symbol, stop_order_data)
        except Exception as e:
            logging.error(f"Error monitoring orders: {e}")

        logging.info("Order plan execution completed")
        return {"status": "success", "order_responses": order_results}
    except Exception as e:
        logging.error(f"Unexpected error in webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("fixed_stop_loss:app", host="0.0.0.0", port=port)
