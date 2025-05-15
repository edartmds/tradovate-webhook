import os
import logging
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from tradovate_api import TradovateClient
import uvicorn
import httpx
import json

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

async def get_latest_price(symbol: str):
    url = f"https://demo-api.tradovate.com/v1/marketdata/quote/{symbol}"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    async with httpx.AsyncClient() as http_client:
        response = await http_client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data["last"]

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

@app.post("/webhook")
async def webhook(req: Request):
    logging.info("Webhook endpoint hit.")
    try:
        content_type = req.headers.get("content-type")
        raw_body = await req.body()

        latest_price = None  # <-- FIXED initialization

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

        action = data["action"].capitalize()
        symbol = data["symbol"]
        if symbol == "CME_MINI:NQ1!":
            symbol = "NQM5"

        order_plan = []
        if "PRICE" in data:
            order_plan.append({"label": "ENTRY", "action": action, "orderType": "Limit", "price": data["PRICE"], "qty": 3})

        for i in range(1, 4):
            key = f"T{i}"
            if key in data:
                order_plan.append({
                    "label": f"TP{i}",
                    "action": "Sell" if action.lower() == "buy" else "Buy",
                    "orderType": "Limit",
                    "price": data[key],
                    "qty": 1
                })

        if "STOP" in data:
            order_plan.append({
                "label": "STOP",
                "action": "Sell" if action.lower() == "buy" else "Buy",
                "orderType": "StopMarket",
                "stopPrice": data["STOP"],
                "qty": 3
            })

        order_results = []
        for order in order_plan:
            order_payload = {
                "accountId": client.account_id,
                "symbol": symbol,
                "action": order["action"],
                "orderQty": order["qty"],
                "orderType": order["orderType"],
                "timeInForce": "GTC",
                "isAutomated": True
            }
            if order["orderType"] in ["Limit", "StopLimit"]:
                order_payload["price"] = order["price"]
            if order["orderType"] in ["StopMarket", "StopLimit"]:
                order_payload["stopPrice"] = order["stopPrice"]

            logging.info(f"Placing {order['label']} order: {order_payload}")
            try:
                result = await client.place_order(
                    symbol=symbol,
                    action=order["action"],
                    quantity=order["qty"],
                    order_data=order_payload
                )
                order_results.append({order["label"]: result})
            except Exception as e:
                logging.error(f"Error placing {order['label']} order: {e}")
                order_results.append({order["label"]: str(e)})

        return {"status": "success", "order_responses": order_results}

    except Exception as e:
        logging.error(f"Unexpected error in webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
