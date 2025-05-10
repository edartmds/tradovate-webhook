import httpx
import os
from dotenv import load_dotenv

load_dotenv()

TRADOVATE_DEMO = os.getenv("TRADOVATE_DEMO", "true") == "true"
BASE_URL = "https://demo-api.tradovate.com/v1" if TRADOVATE_DEMO else "https://live-api.tradovate.com/v1"

class TradovateClient:
    def __init__(self):
        self.access_token = None
        self.account_id = None

    async def authenticate(self):
        url = f"{BASE_URL}/auth/accesstokenrequest"
        auth_payload = {
            "name": os.getenv("TRADOVATE_USERNAME"),
            "password": os.getenv("TRADOVATE_PASSWORD"),
            "appId": os.getenv("TRADOVATE_CLIENT_ID"),
            "appVersion": "1.0",
            "cid": os.getenv("TRADOVATE_CLIENT_ID"),
            "sec": os.getenv("TRADOVATE_CLIENT_SECRET"),
            "deviceId": "webhook-bot"
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=auth_payload)
            r.raise_for_status()
            data = r.json()
            self.access_token = data["accessToken"]

            # Fetch account ID
            headers = {"Authorization": f"Bearer {self.access_token}"}
            acc_res = await client.get(f"{BASE_URL}/account/list", headers=headers)
            acc_res.raise_for_status()
            self.account_id = acc_res.json()[0]["id"]

    async def place_order(self, symbol: str, action: str, quantity: int = 1):
        if not self.access_token:
            await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        order_payload = {
            "accountId": self.account_id,
            "action": action.upper(),  # BUY or SELL
            "symbol": symbol,
            "orderQty": quantity,
            "orderType": "Market",
            "timeInForce": "GTC"
        }

        async with httpx.AsyncClient() as client:
            r = await client.post(f"{BASE_URL}/order/placeorder", json=order_payload, headers=headers)
            r.raise_for_status()
            return r.json()