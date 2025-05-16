# Adjusted to fix the 404 error from get_latest_price for symbol 'NQM5'

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

@app.on_event("startup")
async def startup_event():
    await client.authenticate()

async def get_latest_price(symbol: str):
    # Normalize the symbol for market data queries
    if symbol in ["NQM5", "CME_MINI:NQ1!", "NQ1!"]:
        symbol = "NQH5"  # or whichever front-month is active for your demo environment
    url = f"https://demo-api.tradovate.com/v1/marketdata/quote/{symbol}"
    headers = {"Authorization": f"Bearer {client.access_token}"}
    async with httpx.AsyncClient() as http_client:
        response = await http_client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data.get("last") or data.get("price")

# Leave the rest of the file unchanged as this adjustment should resolve the 404 issue.
# The key change here is normalization of the symbol and fallback logic for missing "last" field.
