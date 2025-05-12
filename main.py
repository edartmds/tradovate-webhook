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

@app.on_event("startup")
async def startup_event():
    # Authenticate the Tradovate client on startup
    await client.authenticate()

async def get_latest_price(symbol: str):
    # Fetch the latest price for the symbol using Tradovate's REST API
    url = f"https://demo-api.tradovate.com/v1/marketdata/quote/{symbol}"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    async with httpx.AsyncClient() as http_client:
        response = await http_client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data["last"]  # Return the last traded price

def parse_alert_to_tradovate_json(alert_text: str, account_id: int) -> dict:
    """
    Converts a plain text alert into a Tradovate JSON payload.

    Args:
        alert_text (str): The plain text alert (e.g., "symbol=CME_MINI:NQ1!,action=Buy,TriggerPrice=20461.75,T1=20470.893,T2=20479.79,T3=20488.81,Stop=20441").
        account_id (int): The Tradovate account ID.

    Returns:
        dict: The JSON payload formatted for Tradovate's API.
    """
    try:
        # Split the alert text into lines and parse key-value pairs
        parsed_data = {}
        for line in alert_text.split("\n"):
            if "=" in line:
                key, value = line.split("=", 1)
                parsed_data[key.strip()] = value.strip()
            elif line.strip().upper() in ["BUY", "SELL"]:
                parsed_data["action"] = line.strip().upper()

        # Validate required fields
        required_fields = ["symbol", "action", "TriggerPrice"]
        for field in required_fields:
            if field not in parsed_data or not parsed_data[field]:
                raise ValueError(f"Missing or invalid field: {field}")

        # Construct the Tradovate JSON payload
        tradovate_payload = {
            "accountId": account_id,
            "action": parsed_data["action"],
            "symbol": parsed_data["symbol"],
            "orderQty": 1,  # Default quantity; adjust as needed
            "orderType": "Stop",  # Assuming Stop order; adjust as needed
            "stopPrice": float(parsed_data["TriggerPrice"]),
            "timeInForce": "GTC",  # Good 'Til Canceled; adjust as needed
            "isAutomated": True
        }

        # Add optional fields like T1, T2, T3, and Stop
        for target in ["T1", "T2", "T3", "Stop"]:
            if target in parsed_data:
                tradovate_payload[target] = float(parsed_data[target])

        return tradovate_payload

    except Exception as e:
        raise ValueError(f"Error parsing alert: {e}")

@app.post("/webhook")
async def webhook(req: Request):
    # Ensure the client is authenticated before processing the webhook
    if not client.account_spec:
        await client.authenticate()

    try:
        content_type = req.headers.get("content-type")
        logging.info(f"Received webhook request with content type: {content_type}")

        if content_type == "application/json":
            data = await req.json()
        elif content_type == "text/plain":
            text_data = await req.body()
            text_data = text_data.decode("utf-8")
            logging.info(f"Received plain text data: {text_data}")
            try:
                # Use the new parser function to convert plain text alerts
                data = parse_alert_to_tradovate_json(text_data, client.account_id)
            except ValueError as e:
                logging.error(f"Error parsing alert: {e}")
                raise HTTPException(status_code=400, detail=str(e))
        else:
            logging.error("Unsupported content type")
            raise HTTPException(status_code=400, detail="Unsupported content type")

        # 🔒 Validate secret token
        if data.get("token") != WEBHOOK_SECRET:
            logging.warning(f"Unauthorized attempt: {data}")
            raise HTTPException(status_code=403, detail="Invalid token")

        logging.info(f"Validated payload: {data}")

        try:
            # Validate required fields for JSON payload
            required_fields = ["symbol", "action", "TriggerPrice", "qty"]
            for field in required_fields:
                if field not in data or not data[field]:
                    raise HTTPException(status_code=400, detail=f"Missing or invalid field: {field}")

            # Construct the primary order payload
            primary_order = {
                "id": 0,  # Placeholder for order ID, update dynamically if needed
                "accountId": client.account_id,
                "accountSpec": client.account_spec,  # Include accountSpec in the payload
                "action": data["action"].upper(),
                "symbol": data["symbol"],
                "orderQty": int(data["qty"]),
                "orderType": "Stop",
                "stopPrice": float(data["TriggerPrice"]),
                "timeInForce": "GTC",
                "isAutomated": True,
                "clOrdId": "string",  # Placeholder for client order ID
                "customTag50": "WebhookOrder"  # Custom tag for identification
            }

            # Construct bracket orders (T1, T2, T3, Stop)
            brackets = []
            for target in ["T1", "T2", "T3"]:
                if target in data and data[target]:
                    brackets.append({
                        "id": 0,  # Placeholder for command ID
                        "orderId": 0,  # Placeholder for parent order ID
                        "action": "SELL" if data["action"].upper() == "BUY" else "BUY",
                        "orderType": "Limit",
                        "price": float(data[target]),
                        "timeInForce": "GTC",
                        "commandType": "New",  # Command type for new orders
                        "commandStatus": "PendingExecution"  # Initial status
                    })

            if "Stop" in data and data["Stop"]:
                brackets.append({
                    "id": 0,  # Placeholder for command ID
                    "orderId": 0,  # Placeholder for parent order ID
                    "action": "SELL" if data["action"].upper() == "BUY" else "BUY",
                    "orderType": "Stop",
                    "stopPrice": float(data["Stop"]),
                    "timeInForce": "GTC",
                    "commandType": "New",  # Command type for new orders
                    "commandStatus": "PendingExecution"  # Initial status
                })

            # Add brackets to the primary order payload
            primary_order["bracket1"] = brackets[0] if len(brackets) > 0 else None
            primary_order["bracket2"] = brackets[1] if len(brackets) > 1 else None

            # Log the payload being sent to Tradovate
            logging.info(f"Primary order payload: {primary_order}")

            # Execute the OSO order on Tradovate
            result = await client.place_oso_order(primary_order)

            logging.info(f"Executed OSO order | Response: {result}")

            return {"status": "success", "order_response": result}

        except ValueError as ve:
            logging.error(f"Validation error: {ve}")
            return {"status": "error", "message": str(ve)}
        except Exception as e:
            logging.error(f"Order failed for {data}: {e}")
            return {"status": "error", "message": str(e)}

    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
