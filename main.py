import os
import logging
from fastapi import FastAPI, HTTPException, Request
import httpx

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

async def get_latest_price(symbol: str):
    url = f"https://demo-api.tradovate.com/v1/marketdata/quote/{symbol}"
    async with httpx.AsyncClient() as http_client:
        response = await http_client.get(url)
        response.raise_for_status()
        data = response.json()
        return data["last"]

@app.post("/webhook")
async def webhook(req: Request):
    logging.info("Webhook endpoint hit.")
    try:
        content_type = req.headers.get("content-type")
        raw_body = await req.body()

        if content_type.startswith("text/plain"):
            alert_text = raw_body.decode("utf-8")
            data = {}
            for line in alert_text.split("\n"):
                if "=" in line:
                    key, value = line.split("=", 1)
                    data[key.strip()] = value.strip()

            required_fields = ["symbol", "action", "PRICE", "STOP", "T1"]
            for field in required_fields:
                if field not in data:
                    raise HTTPException(status_code=400, detail=f"Missing field: {field}")

            symbol = data["symbol"]
            action = data["action"].capitalize()
            entry_price = float(data["PRICE"])
            stop_price = float(data["STOP"])
            take_profit_price = float(data["T1"])

            order_plan = [
                {
                    "label": "ENTRY",
                    "action": action,
                    "orderType": "Stop",
                    "stopPrice": entry_price,
                    "qty": 1
                },
                {
                    "label": "STOP",
                    "action": "Sell" if action.lower() == "buy" else "Buy",
                    "orderType": "Stop",
                    "stopPrice": stop_price,
                    "qty": 1
                },
                {
                    "label": "TP1",
                    "action": "Sell" if action.lower() == "buy" else "Buy",
                    "orderType": "Limit",
                    "price": take_profit_price,
                    "qty": 1
                }
            ]

            logging.info(f"Order plan created: {order_plan}")

            # Simulate placing orders (replace with actual API calls)
            for order in order_plan:
                logging.info(f"Placing order: {order}")

            return {"status": "success", "order_plan": order_plan}

        else:
            raise HTTPException(status_code=400, detail="Unsupported content type")

    except Exception as e:
        logging.error(f"Error in webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=port)
