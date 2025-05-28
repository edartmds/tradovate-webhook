import os
import logging
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

async def cancel_all_orders_and_positions(symbol):
    """Cancel all orders and flatten positions for the given symbol."""
    headers = {"Authorization": f"Bearer {client.access_token}"}

    # Cancel all orders
    list_url = "https://demo-api.tradovate.com/v1/order/list"
    cancel_url = "https://demo-api.tradovate.com/v1/order/cancel"
    async with httpx.AsyncClient() as http_client:
        resp = await http_client.get(list_url, headers=headers)
        resp.raise_for_status()
        orders = resp.json()
        for order in orders:
            if order.get("symbol") == symbol and order.get("status") not in ("Filled", "Cancelled", "Rejected"):
                oid = order.get("id")
                if oid:
                    await http_client.post(f"{cancel_url}/{oid}", headers=headers)

    # Flatten positions
    pos_url = "https://demo-api.tradovate.com/v1/position/list"
    close_url = "https://demo-api.tradovate.com/v1/position/closeposition"
    async with httpx.AsyncClient() as http_client:
        pos_resp = await http_client.get(pos_url, headers=headers)
        pos_resp.raise_for_status()
        positions = pos_resp.json()
        for pos in positions:
            if pos.get("symbol") == symbol and abs(pos.get("netPos", 0)) > 0:
                await http_client.post(close_url, headers=headers, json={"symbol": symbol})

@app.post("/webhook")
async def webhook(req: Request):
    logging.info("Webhook endpoint hit.")
    try:
        content_type = req.headers.get("content-type")
        raw_body = await req.body()

        if content_type == "application/json":
            data = await req.json()
        elif content_type.startswith("text/plain"):
            text_data = raw_body.decode("utf-8")
            data = {}
            for line in text_data.split("\n"):
                if "=" in line:
                    key, value = line.split("=", 1)
                    data[key.strip()] = value.strip()
        else:
            raise HTTPException(status_code=400, detail="Unsupported content type")

        if WEBHOOK_SECRET is None:
            raise HTTPException(status_code=500, detail="Missing WEBHOOK_SECRET")

        symbol = data.get("symbol")
        action = data.get("action")
        price = data.get("PRICE")
        stop_price = data.get("STOP")
        take_profit = data.get("T1")

        if not symbol or not action or not price:
            raise HTTPException(status_code=400, detail="Missing required fields in alert")

        logging.info(f"Received alert for symbol: {symbol}, action: {action}, price: {price}")

        # Cancel all orders and positions
        await cancel_all_orders_and_positions(symbol)

        # Place new orders
        headers = {"Authorization": f"Bearer {client.access_token}"}
        order_payloads = []

        # Entry order
        order_payloads.append({
            "accountId": client.account_id,
            "symbol": symbol,
            "action": action,
            "orderQty": 1,
            "orderType": "Stop",
            "stopPrice": float(price),
            "timeInForce": "GTC",
            "isAutomated": True
        })

        # Stop loss order
        if stop_price:
            order_payloads.append({
                "accountId": client.account_id,
                "symbol": symbol,
                "action": "Sell" if action.lower() == "buy" else "Buy",
                "orderQty": 1,
                "orderType": "Stop",
                "stopPrice": float(stop_price),
                "timeInForce": "GTC",
                "isAutomated": True
            })

        # Take profit order
        if take_profit:
            order_payloads.append({
                "accountId": client.account_id,
                "symbol": symbol,
                "action": "Sell" if action.lower() == "buy" else "Buy",
                "orderQty": 1,
                "orderType": "Limit",
                "price": float(take_profit),
                "timeInForce": "GTC",
                "isAutomated": True
            })

        async with httpx.AsyncClient() as http_client:
            for payload in order_payloads:
                try:
                    response = await http_client.post(
                        "https://demo-api.tradovate.com/v1/order/placeorder",
                        headers=headers,
                        json=payload
                    )
                    response.raise_for_status()
                    logging.info(f"Order placed successfully: {response.json()}")
                except Exception as e:
                    logging.error(f"Error placing order: {e}")

        return {"status": "success", "detail": "Orders placed successfully."}

    except Exception as e:
        logging.error(f"Unexpected error in webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
