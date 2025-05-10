import os
import logging
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from tradovate_api import TradovateClient
import uvicorn

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

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()

    # 🔒 Validate secret token
    if data.get("token") != WEBHOOK_SECRET:
        logging.warning(f"Unauthorized attempt: {data}")
        raise HTTPException(status_code=403, detail="Invalid token")

    try:
        symbol = data["symbol"]
        action = data["action"]
        qty = int(data.get("qty", 1))

        result = await client.place_order(symbol, action, qty)

        logging.info(f"Executed {action.upper()} {qty}x {symbol} | Response: {result}")
        return {"status": "success", "order_response": result}

    except Exception as e:
        logging.error(f"Order failed for {data}: {e}")
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
