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

async def get_current_nq_symbol():
    """
    Dynamically determine the current front-month NASDAQ futures symbol based on the current date.
    """
    current_date = datetime.now()
    year = current_date.year
    month = current_date.month

    # Determine the front-month contract based on the current date
    if month in [12, 1, 2]:
        symbol = f"NQH{str(year + 1)[-2:]}"  # March contract
    elif month in [3, 4, 5]:
        symbol = f"NQM{str(year)[-2:]}"  # June contract
    elif month in [6, 7, 8]:
        symbol = f"NQU{str(year)[-2:]}"  # September contract
    else:
        symbol = f"NQZ{str(year)[-2:]}"  # December contract

    headers = {"Authorization": f"Bearer {client.access_token}"}

    try:
        url = f"https://demo-api.tradovate.com/v1/marketdata/quote/{symbol}"
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                if data.get("last") is not None:
                    logging.info(f"Found valid NQ symbol: {symbol} with price: {data.get('last')}")
                    return symbol
    except Exception as e:
        logging.debug(f"Symbol {symbol} not valid: {e}")

    # Fallback to most likely current symbol
    logging.warning(f"Could not find valid NQ symbol, defaulting to {symbol}")
    return symbol

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
    url = f"https://demo-api.tradovate.com/v1/order/liquidateposition"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    payload = {"symbol": symbol}
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
            logging.info(f"Position flattened for {symbol}: {result}")
            return result
        except Exception as e:
            logging.error(f"Error flattening position for {symbol}: {e}")
            # Try alternative liquidation endpoint
            try:
                alt_url = f"https://demo-api.tradovate.com/v1/position/closeposition"
                response = await http_client.post(alt_url, headers=headers, json=payload)
                response.raise_for_status()
                result = response.json()
                logging.info(f"Position closed via alternative endpoint for {symbol}: {result}")
                return result
            except Exception as e2:
                logging.error(f"Alternative position close also failed for {symbol}: {e2}")
                raise

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

async def place_oco_order(symbol, action, entry_price, take_profit_price, stop_loss_price, quantity=1):
    """
    Place an OCO (One-Cancels-Other) order using Tradovate's OCO functionality.
    This places a take profit and stop loss that automatically cancel each other when one fills.
    """
    headers = {"Authorization": f"Bearer {client.access_token}"}
    
    # Determine the opposite action for TP and SL
    opposite_action = "Sell" if action.lower() == "buy" else "Buy"
    
    # Build OCO payload according to Tradovate's format
    oco_payload = {
        "accountId": client.account_id,
        "symbol": symbol,
        "action": opposite_action,
        "orderQty": quantity,
        "orderType": "Limit",
        "price": take_profit_price,
        "timeInForce": "GTC",
        "other": {
            "accountId": client.account_id,
            "symbol": symbol,
            "action": opposite_action,
            "orderQty": quantity,
            "orderType": "Stop",
            "stopPrice": stop_loss_price,
            "timeInForce": "GTC"
        }
    }
    
    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(
                "https://demo-api.tradovate.com/v1/order/placeoco",
                headers=headers,
                json=oco_payload
            )
            response.raise_for_status()
            result = response.json()
            logging.info(f"OCO order placed successfully: {result}")
            return result
    except Exception as e:
        logging.error(f"Error placing OCO order: {e}")
        raise

async def place_oso_order(symbol, action, entry_price, take_profit_price, stop_loss_price, quantity=1):
    """
    Place an OSO (One-Sends-Other) order using Tradovate's OSO functionality.
    This places an entry order that, when filled, automatically places OCO orders for TP and SL.
    """
    headers = {"Authorization": f"Bearer {client.access_token}"}
    
    # Determine if we need market or stop order for entry
    is_buy = action.lower() == "buy"
    
    # Get current market price to determine entry order type
    try:
        current_price = await get_latest_price(symbol)
        # For BUY: if current price >= entry price, use market. For SELL: if current price <= entry price, use market
        use_market_entry = (is_buy and current_price >= entry_price) or (not is_buy and current_price <= entry_price)
    except:
        use_market_entry = False
        current_price = None
    
    # Determine the opposite action for TP and SL
    opposite_action = "Sell" if is_buy else "Buy"
    
    # Build the OSO payload according to Tradovate's format
    if use_market_entry:
        oso_payload = {
            "accountId": client.account_id,
            "symbol": symbol,
            "action": action,
            "orderQty": quantity,
            "orderType": "Market",
            "timeInForce": "GTC",
            "other": [
                {
                    "accountId": client.account_id,
                    "symbol": symbol,
                    "action": opposite_action,
                    "orderQty": quantity,
                    "orderType": "Limit",
                    "price": take_profit_price,
                    "timeInForce": "GTC"
                },
                {
                    "accountId": client.account_id,
                    "symbol": symbol,
                    "action": opposite_action,
                    "orderQty": quantity,
                    "orderType": "Stop",
                    "stopPrice": stop_loss_price,
                    "timeInForce": "GTC"
                }
            ]
        }
        logging.info(f"Using market order for entry (current: {current_price}, entry: {entry_price})")
    else:
        oso_payload = {
            "accountId": client.account_id,
            "symbol": symbol,
            "action": action,
            "orderQty": quantity,
            "orderType": "Stop",
            "stopPrice": entry_price,
            "timeInForce": "GTC",
            "other": [
                {
                    "accountId": client.account_id,
                    "symbol": symbol,
                    "action": opposite_action,
                    "orderQty": quantity,
                    "orderType": "Limit",
                    "price": take_profit_price,
                    "timeInForce": "GTC"
                },
                {
                    "accountId": client.account_id,
                    "symbol": symbol,
                    "action": opposite_action,
                    "orderQty": quantity,
                    "orderType": "Stop",
                    "stopPrice": stop_loss_price,
                    "timeInForce": "GTC"
                }
            ]
        }
        logging.info(f"Using stop order for entry at price: {entry_price}")
    
    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(
                "https://demo-api.tradovate.com/v1/order/placeoso",
                headers=headers,
                json=oso_payload
            )
            response.raise_for_status()
            result = response.json()
            logging.info(f"OSO order placed successfully: {result}")
            return result
    except Exception as e:
        logging.error(f"Error placing OSO order: {e}")
        # Fallback to manual order placement
        logging.info("Falling back to manual order placement...")
        return await place_manual_orders(symbol, action, entry_price, take_profit_price, stop_loss_price, quantity)

async def place_manual_orders(symbol, action, entry_price, take_profit_price, stop_loss_price, quantity=1):
    """
    Fallback method: Place orders manually and monitor them.
    """
    headers = {"Authorization": f"Bearer {client.access_token}"}
    order_tracking = {"ENTRY": None, "TP": None, "SL": None}
    
    # Determine if we need market or stop order for entry
    is_buy = action.lower() == "buy"
    
    try:
        current_price = await get_latest_price(symbol)
        use_market_entry = (is_buy and current_price >= entry_price) or (not is_buy and current_price <= entry_price)
    except:
        use_market_entry = False
    
    # Place entry order
    if use_market_entry:
        entry_payload = {
            "accountId": client.account_id,
            "symbol": symbol,
            "action": action,
            "orderQty": quantity,
            "orderType": "Market",
            "timeInForce": "GTC"
        }
    else:
        entry_payload = {
            "accountId": client.account_id,
            "symbol": symbol,
            "action": action,
            "orderQty": quantity,
            "orderType": "Stop",
            "stopPrice": entry_price,
            "timeInForce": "GTC"
        }
    
    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(
                "https://demo-api.tradovate.com/v1/order/placeorder",
                headers=headers,
                json=entry_payload
            )
            response.raise_for_status()
            entry_result = response.json()
            order_tracking["ENTRY"] = entry_result.get("id")
            logging.info(f"Entry order placed: {entry_result}")
    except Exception as e:
        logging.error(f"Error placing entry order: {e}")
        return {"error": f"Failed to place entry order: {e}"}
    
    # Start monitoring to place TP and SL after entry fills
    asyncio.create_task(monitor_manual_orders(order_tracking, symbol, action, take_profit_price, stop_loss_price, quantity))
    
    return {"entry_order": entry_result, "monitoring": "started"}

async def monitor_manual_orders(order_tracking, symbol, action, take_profit_price, stop_loss_price, quantity):
    """
    Monitor manual orders and place TP/SL after entry fills, then monitor for completion.
    """
    headers = {"Authorization": f"Bearer {client.access_token}"}
    entry_filled = False
    opposite_action = "Sell" if action.lower() == "buy" else "Buy"
    
    logging.info(f"Starting manual order monitoring for {symbol}")
    
    while True:
        try:
            # Check entry order status
            if order_tracking["ENTRY"] and not entry_filled:
                url = f"https://demo-api.tradovate.com/v1/order/{order_tracking['ENTRY']}"
                async with httpx.AsyncClient() as http_client:
                    response = await http_client.get(url, headers=headers)
                    response.raise_for_status()
                    order_status = response.json()
                
                if order_status.get("status") == "Filled":
                    logging.info("Entry order filled! Placing TP and SL orders...")
                    entry_filled = True
                    
                    # Place Take Profit order
                    tp_payload = {
                        "accountId": client.account_id,
                        "symbol": symbol,
                        "action": opposite_action,
                        "orderQty": quantity,
                        "orderType": "Limit",
                        "price": take_profit_price,
                        "timeInForce": "GTC"
                    }
                    
                    # Place Stop Loss order
                    sl_payload = {
                        "accountId": client.account_id,
                        "symbol": symbol,
                        "action": opposite_action,
                        "orderQty": quantity,
                        "orderType": "Stop",
                        "stopPrice": stop_loss_price,
                        "timeInForce": "GTC"
                    }
                    
                    try:
                        async with httpx.AsyncClient() as http_client:
                            # Place TP order
                            tp_response = await http_client.post(
                                "https://demo-api.tradovate.com/v1/order/placeorder",
                                headers=headers,
                                json=tp_payload
                            )
                            tp_response.raise_for_status()
                            tp_result = tp_response.json()
                            order_tracking["TP"] = tp_result.get("id")
                            logging.info(f"Take Profit order placed: {tp_result}")
                            
                            # Place SL order
                            sl_response = await http_client.post(
                                "https://demo-api.tradovate.com/v1/order/placeorder",
                                headers=headers,
                                json=sl_payload
                            )
                            sl_response.raise_for_status()
                            sl_result = sl_response.json()
                            order_tracking["SL"] = sl_result.get("id")
                            logging.info(f"Stop Loss order placed: {sl_result}")
                            
                    except Exception as e:
                        logging.error(f"Error placing TP/SL orders: {e}")
                        return
            
            # Monitor TP and SL orders if entry is filled
            if entry_filled and order_tracking["TP"] and order_tracking["SL"]:
                # Check TP order
                tp_url = f"https://demo-api.tradovate.com/v1/order/{order_tracking['TP']}"
                sl_url = f"https://demo-api.tradovate.com/v1/order/{order_tracking['SL']}"
                
                async with httpx.AsyncClient() as http_client:
                    tp_response = await http_client.get(tp_url, headers=headers)
                    sl_response = await http_client.get(sl_url, headers=headers)
                    
                    tp_status = tp_response.json() if tp_response.status_code == 200 else {}
                    sl_status = sl_response.json() if sl_response.status_code == 200 else {}
                    
                    # If TP filled, cancel SL
                    if tp_status.get("status") == "Filled":
                        logging.info("Take Profit filled! Cancelling Stop Loss...")
                        try:
                            cancel_response = await http_client.post(
                                f"https://demo-api.tradovate.com/v1/order/cancel/{order_tracking['SL']}",
                                headers=headers
                            )
                            logging.info(f"Stop Loss cancelled after TP fill")
                        except Exception as e:
                            logging.error(f"Error cancelling SL after TP fill: {e}")
                        return
                    
                    # If SL filled, cancel TP
                    if sl_status.get("status") == "Filled":
                        logging.info("Stop Loss filled! Cancelling Take Profit...")
                        try:
                            cancel_response = await http_client.post(
                                f"https://demo-api.tradovate.com/v1/order/cancel/{order_tracking['TP']}",
                                headers=headers
                            )
                            logging.info(f"Take Profit cancelled after SL fill")
                        except Exception as e:
                            logging.error(f"Error cancelling TP after SL fill: {e}")
                        return
            
            await asyncio.sleep(2)  # Check every 2 seconds
            
        except Exception as e:
            logging.error(f"Error in manual order monitoring: {e}")
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
                    data = parse_alert_to_tradovate_json(text_data, client.account_id, latest_price)
                else:
                    raise HTTPException(status_code=400, detail="Invalid alert format")
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
                recent_alert_hashes.pop()

            action = data["action"].capitalize()
            symbol = data["symbol"]

            # Convert to proper Tradovate symbol format
            if symbol in ["CME_MINI:NQ1!", "NQ1!", "NQM5"]:
                symbol = await get_current_nq_symbol()

            # Check if another symbol is currently being processed
            if currently_processing_symbol is not None and currently_processing_symbol != symbol:
                logging.warning(f"Another symbol ({currently_processing_symbol}) is currently being processed. Skipping {symbol}.")
                return {"status": "skipped", "detail": "Another symbol is being processed."}

            # Set the currently processing symbol
            currently_processing_symbol = symbol

            try:
                # Process the alert (placeholder for actual logic)
                logging.info(f"Processing alert for symbol: {symbol}, action: {action}")
            finally:
                currently_processing_symbol = None

        except Exception as e:
            logging.error(f"Unexpected error in webhook: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")

async def monitor_entry_and_place_oco(order_tracking, symbol, action, take_profit_price, stop_loss_price, quantity):
    """
    Monitor entry order and place OCO when it fills.
    """
    headers = {"Authorization": f"Bearer {client.access_token}"}
    logging.info(f"Starting entry monitoring for OCO placement on {symbol}")
    
    while True:
        try:
            if order_tracking["ENTRY"]:
                url = f"https://demo-api.tradovate.com/v1/order/{order_tracking['ENTRY']}"
                async with httpx.AsyncClient() as http_client:
                    response = await http_client.get(url, headers=headers)
                    response.raise_for_status()
                    order_status = response.json()
                
                status = order_status.get("status")
                logging.info(f"Entry order status: {status}")
                
                if status == "Filled":
                    logging.info("Entry filled! Placing OCO for TP/SL...")
                    try:
                        oco_result = await place_oco_order(symbol, action, 0, take_profit_price, stop_loss_price, quantity)
                        logging.info(f"OCO placed after entry fill: {oco_result}")
                        return
                    except Exception as e:
                        logging.error(f"Failed to place OCO after entry fill: {e}")
                        # Fallback to manual TP/SL orders
                        await place_manual_tp_sl(symbol, action, take_profit_price, stop_loss_price, quantity)
                        return
                
                elif status in ["Cancelled", "Rejected"]:
                    logging.warning(f"Entry order was {status} - stopping monitoring")
                    return
            
            await asyncio.sleep(2)
            
        except Exception as e:
            logging.error(f"Error in entry monitoring: {e}")
            await asyncio.sleep(5)

async def place_manual_tp_sl(symbol, action, take_profit_price, stop_loss_price, quantity):
    """
    Place manual TP and SL orders and monitor them for OCO behavior.
    """
    headers = {"Authorization": f"Bearer {client.access_token}"}
    opposite_action = "Sell" if action.lower() == "buy" else "Buy"
    order_tracking = {"TP": None, "SL": None}
    
    try:
        # Place Take Profit order
        tp_payload = {
            "accountId": client.account_id,
            "symbol": symbol,
            "action": opposite_action,
            "orderQty": quantity,
            "orderType": "Limit",
            "price": take_profit_price,
            "timeInForce": "GTC"
        }
        
        # Place Stop Loss order
        sl_payload = {
            "accountId": client.account_id,
            "symbol": symbol,
            "action": opposite_action,
            "orderQty": quantity,
            "orderType": "Stop",
            "stopPrice": stop_loss_price,
            "timeInForce": "GTC"
        }
        
        async with httpx.AsyncClient() as http_client:
            # Place TP order
            tp_response = await http_client.post(
                "https://demo-api.tradovate.com/v1/order/placeorder",
                headers=headers,
                json=tp_payload
            )
            tp_response.raise_for_status()
            tp_result = tp_response.json()
            order_tracking["TP"] = tp_result.get("id")
            logging.info(f"Manual TP order placed: {tp_result}")
            
            # Place SL order
            sl_response = await http_client.post(
                "https://demo-api.tradovate.com/v1/order/placeorder",
                headers=headers,
                json=sl_payload
            )
            sl_response.raise_for_status()
            sl_result = sl_response.json()
            order_tracking["SL"] = sl_result.get("id")
            logging.info(f"Manual SL order placed: {sl_result}")
        
        # Start monitoring for OCO behavior
        asyncio.create_task(monitor_tp_sl_oco(order_tracking, symbol))
        
    except Exception as e:
        logging.error(f"Error placing manual TP/SL orders: {e}")

async def monitor_tp_sl_oco(order_tracking, symbol):
    """
    Monitor TP and SL orders to provide OCO behavior (cancel opposite when one fills).
    """
    headers = {"Authorization": f"Bearer {client.access_token}"}
    logging.info(f"Starting TP/SL OCO monitoring for {symbol}")
    
    while True:
        try:
            if order_tracking["TP"] and order_tracking["SL"]:
                tp_url = f"https://demo-api.tradovate.com/v1/order/{order_tracking['TP']}"
                sl_url = f"https://demo-api.tradovate.com/v1/order/{order_tracking['SL']}"
                
                async with httpx.AsyncClient() as http_client:
                    tp_response = await http_client.get(tp_url, headers=headers)
                    sl_response = await http_client.get(sl_url, headers=headers)
                    
                    tp_status = tp_response.json() if tp_response.status_code == 200 else {}
                    sl_status = sl_response.json() if sl_response.status_code == 200 else {}
                    
                    # If TP filled, cancel SL
                    if tp_status.get("status") == "Filled":
                        logging.info("Take Profit filled! Cancelling Stop Loss...")
                        try:
                            cancel_response = await http_client.post(
                                f"https://demo-api.tradovate.com/v1/order/cancel/{order_tracking['SL']}",
                                headers=headers
                            )
                            logging.info(f"Stop Loss cancelled after TP fill")
                        except Exception as e:
                            logging.error(f"Error cancelling SL after TP fill: {e}")
                        return
                    
                    # If SL filled, cancel TP
                    if sl_status.get("status") == "Filled":
                        logging.info("Stop Loss filled! Cancelling Take Profit...")
                        try:
                            cancel_response = await http_client.post(
                                f"https://demo-api.tradovate.com/v1/order/cancel/{order_tracking['TP']}",
                                headers=headers
                            )
                            logging.info(f"Take Profit cancelled after SL fill")
                        except Exception as e:
                            logging.error(f"Error cancelling TP after SL fill: {e}")
                        return
            
            await asyncio.sleep(2)
            
        except Exception as e:
            logging.error(f"Error in TP/SL OCO monitoring: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
