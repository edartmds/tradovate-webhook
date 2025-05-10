import os
import logging
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from tradovate_api import TradovateClient
import uvicorn
import httpx

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# Create log directory if it doesn't exist
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

# Set up logging
log_file = os.path.join(LOG_DIR, "webhook_trades.log")
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

app = FastAPI()
client = TradovateClient()

async def get_latest_price(symbol: str):
    # Fetch the latest price for the symbol using Tradovate's REST API
    url = f"https://demo-api.tradovate.com/v1/marketdata/quote/{symbol}"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    async with httpx.AsyncClient() as http_client:
        response = await http_client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data["last"]  # Return the last traded price

@app.post("/webhook")
async def webhook(req: Request):
    content_type = req.headers.get("content-type")
    if content_type == "application/json":
        data = await req.json()
    elif content_type == "text/plain":
        text_data = await req.body()
        text_data = text_data.decode("utf-8")
        parsed_data = dict(item.split("=") for item in text_data.split(","))
        data = {
            "symbol": parsed_data["symbol"],
            "action": parsed_data["action"].upper(),
            "TriggerPrice": parsed_data["TriggerPrice"],
            "qty": parsed_data.get("qty", 1),
            "T1": parsed_data.get("T1"),
            "T2": parsed_data.get("T2"),
            "T3": parsed_data.get("T3"),
            "Stop": parsed_data.get("Stop")
        }
    else:
        raise HTTPException(status_code=400, detail="Unsupported content type")

    # 🔒 Validate secret token
    if data.get("token") != WEBHOOK_SECRET:
        logging.warning(f"Unauthorized attempt: {data}")
        raise HTTPException(status_code=403, detail="Invalid token")

    try:
        # Validate required fields for JSON payload
        required_fields = ["symbol", "action", "TriggerPrice", "qty"]
        for field in required_fields:
            if field not in data or not data[field]:
                raise HTTPException(status_code=400, detail=f"Missing or invalid field: {field}")

        # Parse alert information into Tradovate's JSON format for primary order
        primary_order = {
            "symbol": data["symbol"],
            "action": data["action"].upper(),
            "orderQty": int(data["qty"]),
            "orderType": "Stop",
            "stopPrice": float(data["TriggerPrice"]),
            "timeInForce": "GTC"
        }

        # Create additional stop orders for targets (T1, T2, T3) and stop loss
        additional_orders = []
        for target in ["T1", "T2", "T3"]:
            if target in data and data[target]:
                additional_orders.append({
                    "symbol": data["symbol"],
                    "action": data["action"].upper(),
                    "orderQty": int(data["qty"]),
                    "orderType": "Stop",
                    "stopPrice": float(data[target]),
                    "timeInForce": "GTC"
                })

        if "Stop" in data and data["Stop"]:
            additional_orders.append({
                "symbol": data["symbol"],
                "action": "SELL" if data["action"].upper() == "BUY" else "BUY",
                "orderQty": int(data["qty"]),
                "orderType": "Stop",
                "stopPrice": float(data["Stop"]),
                "timeInForce": "GTC"
            })

        # Validate the JSON format before sending to Tradovate
        logging.info("Validating JSON format for primary and additional orders...")
        logging.info(f"Primary order JSON: {primary_order}")
        for idx, order in enumerate(additional_orders):
            logging.info(f"Additional order {idx + 1} JSON: {order}")

        # Log the payload being sent to Tradovate
        logging.info("Sending primary order payload to Tradovate API...")
        logging.info(f"Primary order payload: {primary_order}")

        # Log additional orders payloads
        for idx, order in enumerate(additional_orders):
            logging.info(f"Sending additional order {idx + 1} payload: {order}")

        # Ensure all required fields are present in the payloads
        required_fields = ["accountId", "action", "symbol", "orderQty", "orderType", "timeInForce"]
        for field in required_fields:
            if field not in primary_order or primary_order[field] is None:
                raise HTTPException(status_code=400, detail=f"Missing or invalid field in primary order: {field}")

        for idx, order in enumerate(additional_orders):
            for field in required_fields:
                if field not in order or order[field] is None:
                    raise HTTPException(status_code=400, detail=f"Missing or invalid field in additional order {idx + 1}: {field}")

        # Debugging logs to confirm parsed orders
        logging.info(f"Primary order: {primary_order}")
        logging.info(f"Additional orders: {additional_orders}")

        symbol = data["symbol"]
        action = data["action"]
        qty = int(data["qty"])

        # Execute the order on Tradovate
        result = await client.place_order(symbol, action, qty, primary_order)

        logging.info(f"Executed {action.upper()} {qty}x {symbol} | Response: {result}")

        # Execute additional stop orders
        for order in additional_orders:
            additional_result = await client.place_order(
                order["symbol"], order["action"], order["orderQty"], order
            )
            logging.info(f"Executed additional order: {additional_result}")

        return {"status": "success", "order_response": result, "additional_orders": additional_orders}

    except ValueError as ve:
        logging.error(f"Validation error: {ve}")
        return {"status": "error", "message": str(ve)}
    except Exception as e:
        logging.error(f"Order failed for {data}: {e}")
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
