import httpx
import os
import logging
import json  # Added for pretty-printing JSON responses
from dotenv import load_dotenv
from fastapi import HTTPException

load_dotenv()

TRADOVATE_DEMO = os.getenv("TRADOVATE_DEMO", "true") == "true"
BASE_URL = "https://demo-api.tradovate.com/v1" if TRADOVATE_DEMO else "https://live-api.tradovate.com/v1"

class TradovateClient:
    def __init__(self):
        self.access_token = None
        self.account_id = None
        self.account_spec = None

    async def authenticate(self):
        url = f"{BASE_URL}/auth/accesstokenrequest"
        auth_payload = {
            "name": os.getenv("TRADOVATE_USERNAME"),
            "password": os.getenv("TRADOVATE_PASSWORD"),
            "appId": os.getenv("TRADOVATE_APP_ID"),
            "appVersion": os.getenv("TRADOVATE_APP_VERSION"),
            "cid": os.getenv("TRADOVATE_CLIENT_ID"),
            "sec": os.getenv("TRADOVATE_CLIENT_SECRET"),
            "deviceId": os.getenv("TRADOVATE_DEVICE_ID")
        }
        try:
            async with httpx.AsyncClient() as client:
                logging.debug(f"Sending authentication payload: {json.dumps(auth_payload, indent=2)}")
                r = await client.post(url, json=auth_payload)
                r.raise_for_status()
                data = r.json()
                logging.info(f"Authentication response: {json.dumps(data, indent=2)}")
                self.access_token = data["accessToken"]

                # Fetch account ID
                headers = {"Authorization": f"Bearer {self.access_token}"}
                acc_res = await client.get(f"{BASE_URL}/account/list", headers=headers)
                acc_res.raise_for_status()
                account_data = acc_res.json()
                logging.info(f"Account list response: {json.dumps(account_data, indent=2)}")
                self.account_id = account_data[0]["id"]
                self.account_spec = account_data[0].get("name")

                # Use hardcoded values from .env if available
                self.account_id = int(os.getenv("TRADOVATE_ACCOUNT_ID", self.account_id))
                self.account_spec = os.getenv("TRADOVATE_ACCOUNT_SPEC", self.account_spec)

                logging.info(f"Using account_id: {self.account_id} and account_spec: {self.account_spec} from environment variables.")

                if not self.account_spec:
                    logging.error("Failed to retrieve accountSpec. accountSpec is None.")
                    raise HTTPException(status_code=400, detail="Failed to retrieve accountSpec")

                # Log the retrieved accountSpec and accountId for debugging
                logging.info(f"Retrieved accountSpec: {self.account_spec}")
                logging.info(f"Retrieved accountId: {self.account_id}")

                if not self.account_id:
                    logging.error("Failed to retrieve account ID. Account ID is None.")
                    raise HTTPException(status_code=400, detail="Failed to retrieve account ID")

                logging.info("Authentication successful. Access token, accountSpec, and account ID retrieved.")
        except httpx.HTTPStatusError as e:
            logging.error(f"Authentication failed: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail="Authentication failed")
        except Exception as e:
            logging.error(f"Unexpected error during authentication: {e}")
            raise HTTPException(status_code=500, detail="Internal server error during authentication")

    async def place_order(self, symbol: str, action: str, quantity: int = 1, order_data: dict = None):
        if not self.access_token:
            await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        # Use the provided order_data if available, otherwise construct a default payload
        order_payload = order_data or {
            "accountId": self.account_id,
            "action": action.capitalize(),  # Ensure "Buy" or "Sell"
            "symbol": symbol,
            "orderQty": quantity,
            "orderType": "limit",
            "timeInForce": "GTC",
            "isAutomated": True  # Optional field for automation
        }

        if not order_payload.get("accountId"):
            logging.error("Missing accountId in order payload.")
            raise HTTPException(status_code=400, detail="Missing accountId in order payload")

        try:
            async with httpx.AsyncClient() as client:
                logging.debug(f"Sending order payload: {json.dumps(order_payload, indent=2)}")
                r = await client.post(f"{BASE_URL}/order/placeorder", json=order_payload, headers=headers)
                r.raise_for_status()
                response_data = r.json()
                logging.info(f"Order placement response: {json.dumps(response_data, indent=2)}")
                return response_data
        except httpx.HTTPStatusError as e:
            logging.error(f"Order placement failed: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Order placement failed: {e.response.text}")
        except Exception as e:
            logging.error(f"Unexpected error during order placement: {e}")
            raise HTTPException(status_code=500, detail="Internal server error during order placement")

    async def place_oso_order(self, order_payload: dict):
        """
        Places an OSO (Order Sends Order) order on Tradovate.

        Args:
            order_payload (dict): The JSON payload for the OSO order.

        Returns:
            dict: The response from the Tradovate API.
        """
        if not self.access_token:
            await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        try:
            async with httpx.AsyncClient() as client:
                logging.debug(f"Sending OSO order payload: {json.dumps(order_payload, indent=2)}")
                response = await client.post(f"{BASE_URL}/order/oso", json=order_payload, headers=headers)
                response.raise_for_status()
                response_data = response.json()
                logging.info(f"OSO order response: {json.dumps(response_data, indent=2)}")
                return response_data
        except httpx.HTTPStatusError as e:
            logging.error(f"OSO order placement failed: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"OSO order placement failed: {e.response.text}")
        except Exception as e:
            logging.error(f"Unexpected error during OSO order placement: {e}")
            raise HTTPException(status_code=500, detail="Internal server error during OSO order placement")

    async def place_oso_order(self, initial_order: dict):
        """
        Places an Order Sends Order (OSO) order on Tradovate.

        Args:
            initial_order (dict): The JSON payload for the initial order with brackets.

        Returns:
            dict: The response from the Tradovate API.
        """
        if not self.access_token:
            await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        try:
            async with httpx.AsyncClient() as client:
                logging.debug(f"Sending OSO order payload: {json.dumps(initial_order, indent=2)}")
                response = await client.post(f"{BASE_URL}/order/placeoso", json=initial_order, headers=headers)
                response.raise_for_status()
                response_data = response.json()
                logging.info(f"OSO order response: {json.dumps(response_data, indent=2)}")
                return response_data
        except httpx.HTTPStatusError as e:
            logging.error(f"OSO order placement failed: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"OSO order placement failed: {e.response.text}")
        except Exception as e:
            logging.error(f"Unexpected error during OSO order placement: {e}")
            raise HTTPException(status_code=500, detail="Internal server error during OSO order placement")
