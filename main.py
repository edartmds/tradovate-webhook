from fastapi import FastAPI, Request
from tradovate_api import TradovateClient

app = FastAPI()
client = TradovateClient()

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()

    try:
        symbol = data["symbol"]
        action = data["action"]
        qty = int(data.get("qty", 1))

        result = await client.place_order(symbol, action, qty)
        return {"status": "success", "order_response": result}

    except Exception as e:
        return {"status": "error", "message": str(e)}
