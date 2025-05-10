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
        # Parse alert information into Tradovate's JSON format for stop orders
        data = {
            "symbol": parsed_data["symbol"],
            "action": parsed_data["action"].upper(),
            "orderQty": int(parsed_data.get("qty", 1)),
            "orderType": "Stop",  # Default to Stop order
            "stopPrice": float(parsed_data["TriggerPrice"]),
            "timeInForce": "GTC"
        }

        # Create additional stop orders for targets and stop loss
        additional_orders = []
        for target in ["T1", "T2", "T3"]:
            if target in parsed_data:
                additional_orders.append({
                    "symbol": parsed_data["symbol"],
                    "action": parsed_data["action"].upper(),
                    "orderQty": int(parsed_data.get("qty", 1)),
                    "orderType": "Stop",
                    "stopPrice": float(parsed_data[target]),
                    "timeInForce": "GTC"
                })

        # Add stop loss as a separate stop order
        if "Stop" in parsed_data:
            additional_orders.append({
                "symbol": parsed_data["symbol"],
                "action": "SELL" if parsed_data["action"].upper() == "BUY" else "BUY",  # Reverse action for stop loss
                "orderQty": int(parsed_data.get("qty", 1)),
                "orderType": "Stop",
                "stopPrice": float(parsed_data["Stop"]),
                "timeInForce": "GTC"
            })
    else:
        raise HTTPException(status_code=400, detail="Unsupported content type")

    # 🔒 Validate secret token
    if data.get("token") != WEBHOOK_SECRET:
        logging.warning(f"Unauthorized attempt: {data}")
        raise HTTPException(status_code=403, detail="Invalid token")

    try:
        symbol = data["symbol"]
        action = data["action"]
        qty = data.get("orderQty")

        if qty is None:
            raise ValueError("Missing 'orderQty' in the payload")

        # Execute the order on Tradovate
        result = await client.place_order(symbol, action, qty, data)

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
