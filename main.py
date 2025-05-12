import os
import logging
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from tradovate_api import TradovateClient
import uvicorn
import httpx
import json

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
logging.info(f"Loaded WEBHOOK_SECRET: {WEBHOOK_SECRET}")  # Debugging purpose only, remove in production

# Create log directory if it doesn't exist
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

# Set up logging
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

        # Handle JSON-like structure at the start of the alert text
        if alert_text.startswith("="):
            try:
                json_part, remaining_text = alert_text[1:].split("\n", 1)
                json_data = json.loads(json_part)
                parsed_data.update(json_data)
                alert_text = remaining_text
            except (json.JSONDecodeError, ValueError) as e:
                raise ValueError(f"Error parsing JSON-like structure: {e}")

        # Handle special cases like settlement-as-close
        if "settlement-as-close" in alert_text:
            parsed_data["settlementAsClose"] = True

        # Map TradingView symbols to Tradovate-compatible symbols
        symbol_mapping = {
            "CME_MINI:NQ1!": "NQM5",
            "CME_MINI:ES1!": "ESM5"
        }
        if "symbol" in parsed_data and parsed_data["symbol"] in symbol_mapping:
            parsed_data["symbol"] = symbol_mapping[parsed_data["symbol"]]

        # Adjust parsing for the remaining text
        for line in alert_text.split("\n"):
            if "=" in line:
                key, value = line.split("=", 1)
                parsed_data[key.strip()] = value.strip()
            elif line.strip().upper() in ["BUY", "SELL"]:
                parsed_data["action"] = line.strip().capitalize()

        # Validate required fields
        required_fields = ["symbol", "action", "PRICE"]
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
            "stopPrice": float(parsed_data["PRICE"]),
            "timeInForce": "GTC",  # Good 'Til Canceled; adjust as needed
            "isAutomated": True
        }

        # Add optional fields like T1, T2, T3, and STOP
        for target in ["T1", "T2", "T3", "STOP"]:
            if target in parsed_data:
                tradovate_payload[target.lower()] = float(parsed_data[target])

        return tradovate_payload

    except Exception as e:
        raise ValueError(f"Error parsing alert: {e}")

@app.post("/webhook")
async def webhook(req: Request):
    logging.info("Webhook endpoint hit. Request received.")

    # Ensure the client is authenticated and account_spec is set
    if not client.account_spec:
        logging.info("Authenticating client as account_spec is not set.")
        await client.authenticate()

    try:
        content_type = req.headers.get("content-type")
        logging.info(f"Received webhook request with content type: {content_type}")

        if content_type == "application/json":
            data = await req.json()
        elif content_type.startswith("text/plain"):
            text_data = await req.body()
            text_data = text_data.decode("utf-8")
            logging.info(f"Received plain text data: {text_data}")
            try:
                # Parse the plain text alert into JSON
                data = parse_alert_to_tradovate_json(text_data, client.account_id)
            except ValueError as e:
                logging.error(f"Error parsing alert: {e}")
                raise HTTPException(status_code=400, detail=str(e))
        else:
            logging.error(f"Unsupported content type: {content_type}")
            raise HTTPException(status_code=400, detail=f"Unsupported content type: {content_type}")

        # Hardcode the WEBHOOK_SECRET for validation
        if WEBHOOK_SECRET is None:
            logging.error("WEBHOOK_SECRET is not set in the environment variables.")
            raise HTTPException(status_code=500, detail="Server configuration error: WEBHOOK_SECRET is missing.")

        # Skip token validation since it is hardcoded
        logging.info("Skipping token validation as WEBHOOK_SECRET is hardcoded.")

        logging.info(f"Validated payload: {data}")

        # Construct the OSO order payload specific to Tradovate API
        oso_order = {
            "accountSpec": client.account_spec,
            "accountId": client.account_id,
            "action": "Buy",
            "symbol": "MESM1",
            "orderQty": 1,
            "orderType": "Limit",
            "price": 4150.00,
            "isAutomated": True,
            "bracket1": {
                "action": "Sell",
                "orderType": "Limit",
                "price": 4200.00
            }
        }

        logging.info(f"OSO order payload: {oso_order}")

        # Place the OSO order
        result = await client.place_oso_order(oso_order)

        logging.info(f"Executed OSO order | Response: {result}")

        return {"status": "success", "order_response": result}

    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
