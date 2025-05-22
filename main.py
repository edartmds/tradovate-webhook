import os
import logging
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from tradovate_api import TradovateClient
import uvicorn
import httpx
import json
import hashlib
import asyncio

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
recent_alert_hashes = set()
MAX_HASHES = 20  # Keep the last 20 unique alerts

@app.on_event("startup")
async def startup_event():
    await client.authenticate()

async def ensure_authenticated():
    if not client.access_token:
        logging.warning("Access token is missing. Re-authenticating...")
        await client.authenticate()
    else:
        logging.info("Access token is present.")

async def get_latest_price(symbol: str):
    await ensure_authenticated()
    symbol_map = {
        "CME_MINI:NQ1!": "NQH5",
        "NQ1!": "NQH5",
        "NQM5": "NQH5"
    }
    normalized_symbol = symbol_map.get(symbol, symbol)
    url = f"https://demo-api.tradovate.com/v1/marketdata/quote/{normalized_symbol}"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    async with httpx.AsyncClient() as http_client:
        response = await http_client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data.get("last") or data.get("price")

# ------------------ WHAT YOU ADDED / MODIFIED ------------------

# ✅ Updated logic for order creation: ENTRY (Stop), T1–T3 (Limit), STOP (Stop)
order_plan = []
if "PRICE" in data:
    order_plan.append({
        "label": "ENTRY",
        "action": action,
        "orderType": "Stop",
        "price": data["PRICE"],
        "qty": 3
    })

for i in range(1, 4):
    key = f"T{i}"
    if key in data:
        order_plan.append({
            "label": f"TP{i}",
            "action": "Sell" if action.lower() == "buy" else "Buy",
            "orderType": "Limit",  # ✅ TAKE PROFIT = LIMIT
            "price": data[key],
            "qty": 1
        })

if "STOP" in data:
    order_plan.append({
        "label": "STOP",
        "action": "Sell" if action.lower() == "buy" else "Buy",
        "orderType": "Stop",  # ✅ STOP LOSS = STOP
        "stopPrice": data["STOP"],
        "qty": 3
    })
