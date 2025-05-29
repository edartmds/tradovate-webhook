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
active_orders = []  # Track active order IDs to manage cancellation

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
    logging.info("=== APPLICATION STARTING UP ===")
    try:
        await client.authenticate()
        logging.info(f"=== AUTHENTICATION SUCCESSFUL ===")
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

# REMOVED: Legacy stop loss function - now handled by OSO bracket orders
# REMOVED: Legacy monitor_all_orders function that was interfering with OSO bracket orders
# OSO (Order Sends Order) handles bracket order management automatically
# Manual order monitoring is no longer needed


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

        # Extract required fields
        symbol = data.get("symbol")
        action = data.get("action")
        price = data.get("PRICE")
        t1 = data.get("T1")
        stop = data.get("STOP")

        logging.info(f"Extracted fields - Symbol: {symbol}, Action: {action}, Price: {price}, T1: {t1}, Stop: {stop}")

        if not all([symbol, action, price, t1, stop]):
            missing = [k for k, v in {"symbol": symbol, "action": action, "PRICE": price, "T1": t1, "STOP": stop}.items() if not v]
            logging.error(f"Missing required fields: {missing}")
            raise HTTPException(status_code=400, detail=f"Missing required fields: {missing}")        # Map TradingView symbol to Tradovate symbol
        if symbol == "CME_MINI:NQ1!" or symbol == "NQ1!":
            symbol = "NQM5"
            logging.info(f"Mapped symbol to: {symbol}")        # STEP 1: Cancel all existing pending orders first (TP/SL from previous alerts)
        logging.info("=== CANCELLING ALL PENDING ORDERS ===")
        try:
            cancelled_orders = await client.cancel_all_pending_orders()
            logging.info(f"Successfully cancelled {len(cancelled_orders)} pending orders")
        except Exception as e:
            logging.warning(f"Failed to cancel some orders: {e}")
            # Continue with position closure even if cancellation partially fails

        # STEP 2: Close all existing positions (including any position left open after cancelling TP/SL)
        logging.info("=== CLOSING ALL EXISTING POSITIONS ===")
        try:
            closed_positions = await client.close_all_positions()
            logging.info(f"Successfully closed {len(closed_positions)} positions")
            
            # Add small delay to ensure market orders are fully processed
            if closed_positions:
                logging.info("Waiting for position closure to fully process...")
                await asyncio.sleep(2)  # 2 second delay for market order execution
                
        except Exception as e:
            logging.warning(f"Failed to close some positions: {e}")
            # Continue even if position closure partially fails

        # STEP 2.5: Add extra delay to ensure cleanup is complete before placing new orders
        logging.info("=== ENSURING CLEAN SLATE BEFORE NEW ORDERS ===")
        await asyncio.sleep(1)  # Additional delay to ensure all cleanup is complete# STEP 3: Create OSO bracket orders (entry + take profit + stop loss)
        logging.info(f"=== CREATING OSO BRACKET ORDERS ===")
        logging.info(f"Symbol: {symbol}, Entry: {price}, TP: {t1}, SL: {stop}")
        
        # Determine opposite action for take profit and stop loss
        opposite_action = "Sell" if action.lower() == "buy" else "Buy"
        
        # Create OSO payload with entry order and two bracket orders (TP + SL)
        # Ensure all required fields are present in all orders
        oso_payload = {
            "accountSpec": client.account_spec,
            "accountId": client.account_id,
            "action": action,
            "symbol": symbol,
            "orderQty": 1,
            "orderType": "Stop",
            "stopPrice": price,  # Entry at PRICE level using stop order
            "timeInForce": "GTC",
            "isAutomated": True,
            "bracket1": {
                "accountSpec": client.account_spec,
                "accountId": client.account_id,
                "action": opposite_action,
                "symbol": symbol,
                "orderQty": 1,
                "orderType": "Limit",
                "price": t1,  # Take profit at T1 level
                "timeInForce": "GTC",
                "isAutomated": True
            },
            "bracket2": {
                "accountSpec": client.account_spec,
                "accountId": client.account_id,
                "action": opposite_action,
                "symbol": symbol,
                "orderQty": 1,
                "orderType": "Stop",
                "stopPrice": stop,  # Stop loss at STOP level
                "timeInForce": "GTC",
                "isAutomated": True
            }
        }
        
        logging.info(f"=== OSO BRACKET PAYLOAD ===")
        logging.info(f"{json.dumps(oso_payload, indent=2)}")

        # STEP 4: Place OSO bracket order (entry + take profit + stop loss)
        logging.info("=== PLACING OSO BRACKET ORDER ===")
        try:
            oso_result = await client.place_oso_order(oso_payload)
            logging.info(f"=== OSO BRACKET ORDER PLACED SUCCESSFULLY ===")
            logging.info(f"OSO Result: {oso_result}")
            
            return {"status": "success", "order": oso_result}
            
        except Exception as e:
            logging.error(f"OSO bracket order placement failed: {e}")
            import traceback
            logging.error(f"Traceback: {traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"Failed to place bracket order: {str(e)}")

    except Exception as e:
        logging.error(f"=== ERROR IN WEBHOOK ===")
        logging.error(f"Error: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
