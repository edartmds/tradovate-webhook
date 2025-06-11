import asyncio
import httpx
import pandas as pd
from datetime import datetime, timedelta
import os

# Load credentials from environment variables
USERNAME = os.getenv("TRADOVATE_USERNAME")
PASSWORD = os.getenv("TRADOVATE_PASSWORD")
APP_ID = os.getenv("TRADOVATE_APP_ID")
APP_VERSION = os.getenv("TRADOVATE_APP_VERSION")
DEVICE_ID = os.getenv("TRADOVATE_DEVICE_ID")

# API endpoints
BASE_URL = "https://demo-api.tradovate.com/v1"
MD_BASE_URL = "https://md-demo.tradovateapi.com/v1"
MD_URL = f"{MD_BASE_URL}/md/getchart"
AUTH_URL = f"{BASE_URL}/auth/accesstokenrequest"
CONTRACT_FIND_URL = f"{BASE_URL}/contract/find"

# Date range for full year
START_DATE = datetime(2024, 6, 10)
END_DATE = datetime(2025, 6, 10)
BATCH_DELTA = timedelta(days=1)  # 1-day batches (~1440 bars < 5000 limit)

async def get_access_token(client: httpx.AsyncClient) -> tuple[str, str]:
    # Return both access token and market data token
    access_token = "DsUTwCsZI4Yc6vAhiP9E9usmEmALwOaDSrK4nSdwlFr_Bv9AHM53Z4wP4JF81nQgRzL-KFINujeGYOzZXM91M2Xy29gdFTOGlWmT57V0uT-Q4dHJGBA1Sk5c0lj8U2fTXuJU2Jg0or-62jIPMymgDJLXzq_IE5G5j0rhkrAjVfLg46ILsAA-fhH4u52WJ09nxqwy-NockO00i"
    md_access_token = "IP00jZ4ONmHeDaFZwSGb1GFPhxUM_I4yYEseTOnUeW0L7T1zbbU87meMRAkNJjABVpPQ35cPKxIw6NMPnTgNrkyCqVmZp9-PdubFFj8FSNgrcAp_F-wtKRstlXNtniyK6LlHR5hwxXcKQ7u93ay_nnR8eqBOPF4ccBD8TWDsGFifsOxz6Q6Gn8a21Rn0RxIKOI8kvpoJPLsh5UqE"
    return access_token, md_access_token

async def get_contract_id(client: httpx.AsyncClient, token: str) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    params = {"name": "NQM5", "demo": True}  # Ensure demo environment is specified
    resp = await client.get(CONTRACT_FIND_URL, params=params, headers=headers, timeout=20)
    print("Response Content:", resp.text)  # Debugging line to log response content
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise RuntimeError("Contract NQM5 not found")
    return data[0].get("contractId") or data[0].get("id")

async def fetch_bars(client: httpx.AsyncClient, md_token: str, batch_start: datetime, batch_end: datetime) -> list:
    headers = {"Authorization": f"Bearer {md_token}", "Content-Type": "application/json"}
    payload = {
        "symbol": "NQM5",  # Updated to use NQM5
        "chartDescription": {
            "underlyingType": "MinuteBar",
            "elementSize": 1,
            "elementSizeUnit": "UnderlyingUnits"
        },
        "timeRange": {
            "asMuchAsElements": 5000,
            "asTill": batch_end.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        }
    }
    resp = await client.post(MD_URL, headers=headers, json=payload, timeout=60)
    print(f"Market data response status: {resp.status_code}")
    print(f"Market data response: {resp.text}")
    resp.raise_for_status()
    data = resp.json()
    return data.get("bars", [])

async def main():
    async with httpx.AsyncClient() as client:
        print("Getting access tokens...")
        access_token, md_token = await get_access_token(client)
        print("Tokens acquired. Skipping contract ID lookup and proceeding with market data...")
        
        all_bars = []
        current = START_DATE
        print(f"Starting data fetch from {START_DATE} to {END_DATE} in daily batches...")
        while current < END_DATE:
            batch_start = current
            batch_end = min(current + BATCH_DELTA, END_DATE)
            print(f"Fetching bars: {batch_start} to {batch_end}")
            try:
                bars = await fetch_bars(client, md_token, batch_start, batch_end)
                all_bars.extend(bars)
                print(f"  Retrieved {len(bars)} bars")
            except Exception as e:
                print(f"  Error fetching batch: {e}")
            current += BATCH_DELTA

        print(f"Fetched total {len(all_bars)} bars. Saving to CSV...")
        # Normalize to DataFrame and save
        df = pd.DataFrame(all_bars)
        # Convert timestamp to datetime if present
        if "t" in df.columns:
            df["timestamp"] = pd.to_datetime(df["t"], unit='ms')
        df.to_csv("nq1m_20240610_20250610.csv", index=False)
        print("✅ Saved CSV: nq1m_20240610_20250610.csv")

if __name__ == "__main__":
    asyncio.run(main())
