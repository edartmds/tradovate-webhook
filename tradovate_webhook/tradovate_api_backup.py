# 🚀 SPEED OPTIMIZED VERSION - TRADOVATE API CLIENT
import httpx
import os
import logging
import json
import asyncio
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
        # 🚀 SPEED OPTIMIZATION: Connection pooling for faster HTTP requests
        self._http_client = None
        self._client_timeout = httpx.Timeout(connect=3.0, read=8.0, write=3.0, pool=None)  # Reduced timeouts
        self._connection_limits = httpx.Limits(max_keepalive_connections=15, max_connections=30)  # Increased limits

    async def _get_http_client(self):
        """🚀 SPEED OPTIMIZATION: Get reusable HTTP client with connection pooling"""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=self._client_timeout,
                limits=self._connection_limits
            )
        return self._http_client

    async def close_client(self):
        """Close the HTTP client connection pool"""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

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
        max_retries = 3  # 🚀 SPEED: Reduced retries
        backoff_factor = 1.5  # 🚀 SPEED: Faster retry backoff

        for attempt in range(max_retries):
            try:
                # 🚀 SPEED OPTIMIZATION: Use persistent HTTP client
                client = await self._get_http_client()
                logging.debug(f"Sending authentication payload: {json.dumps(auth_payload, indent=2)}")
                r = await client.post(url, json=auth_payload)
                r.raise_for_status()
                data = r.json()
                logging.info(f"Authentication response: {json.dumps(data, indent=2)}")
                self.access_token = data["accessToken"]

                # 🚀 SPEED: Parallel fetch account ID using the same client
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

                logging.info(f"Retrieved accountSpec: {self.account_spec}")
                logging.info(f"Retrieved accountId: {self.account_id}")

                if not self.account_id:
                    logging.error("Failed to retrieve account ID. Account ID is None.")
                    raise HTTPException(status_code=400, detail="Failed to retrieve account ID")

                logging.info("Authentication successful. Access token, accountSpec, and account ID retrieved.")
                return  # Exit the retry loop on success

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:  # Handle rate-limiting
                    retry_after = int(e.response.headers.get("Retry-After", backoff_factor * (attempt + 1)))
                    logging.warning(f"Rate-limited (429). Retrying after {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                else:
                    logging.error(f"Authentication failed: {e.response.text}")
                    raise HTTPException(status_code=e.response.status_code, detail="Authentication failed")
            except Exception as e:
                logging.error(f"Unexpected error during authentication: {e}")
                raise HTTPException(status_code=500, detail="Internal server error during authentication")

        logging.error("Max retries reached. Authentication failed.")
        raise HTTPException(status_code=429, detail="Authentication failed after maximum retries")

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
            # 🚀 SPEED: Use persistent client
            client = await self._get_http_client()
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

    async def place_oso_order(self, initial_order: dict):
        """
        Places an Order Sends Order (OSO) order on Tradovate.
        🚀 SPEED OPTIMIZED VERSION
        """
        if not self.access_token:
            await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        try:
            # 🚀 SPEED: Use persistent client
            client = await self._get_http_client()
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

    async def get_pending_orders(self):
        """
        🚀 SPEED OPTIMIZED: Retrieves all pending orders for the authenticated account.
        """
        if not self.access_token:
            await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        try:
            # 🚀 SPEED: Use persistent client
            client = await self._get_http_client()
            response = await client.get(f"{BASE_URL}/order/list", headers=headers)
            response.raise_for_status()
            orders = response.json()
            
            # Filter for pending orders only
            pending_orders = [order for order in orders if order.get("ordStatus") in ["Pending", "Working", "Submitted"]]
            logging.info(f"Found {len(pending_orders)} pending orders")
            logging.debug(f"Pending orders: {json.dumps(pending_orders, indent=2)}")
            return pending_orders
                
        except httpx.HTTPStatusError as e:
            logging.error(f"Failed to get orders: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Failed to get orders: {e.response.text}")
        except Exception as e:
            logging.error(f"Unexpected error getting orders: {e}")
            raise HTTPException(status_code=500, detail="Internal server error getting orders")

    async def cancel_order(self, order_id: int):
        """
        🚀 SPEED OPTIMIZED: Cancels a specific order by ID.
        """
        if not self.access_token:
            await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        cancel_payload = {
            "orderId": order_id
        }

        try:
            # 🚀 SPEED: Use persistent client
            client = await self._get_http_client()
            logging.debug(f"Canceling order {order_id}")
            response = await client.post(f"{BASE_URL}/order/cancelorder", json=cancel_payload, headers=headers)
            response.raise_for_status()
            response_data = response.json()
            logging.info(f"Order {order_id} cancelled successfully: {json.dumps(response_data, indent=2)}")
            return response_data
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logging.info(f"Order {order_id} already filled/cancelled (404 - not found)")
                return {"status": "already_handled", "message": "Order already filled or cancelled"}
            else:
                logging.error(f"Failed to cancel order {order_id}: {e.response.text}")
                raise HTTPException(status_code=e.response.status_code, detail=f"Failed to cancel order: {e.response.text}")
        except Exception as e:
            logging.error(f"Unexpected error canceling order {order_id}: {e}")
            raise HTTPException(status_code=500, detail="Internal server error canceling order")

    async def cancel_all_pending_orders(self):
        """
        🚀 SPEED OPTIMIZED: Cancels all pending orders with parallel processing
        """
        try:
            pending_orders = await self.get_pending_orders()
            
            if not pending_orders:
                logging.info("No pending orders to cancel")
                return []
            
            # 🚀 SPEED OPTIMIZATION: Parallel order cancellation
            cancel_tasks = []
            for order in pending_orders:
                order_id = order.get("id")
                if order_id:
                    cancel_tasks.append(self._cancel_order_fast(order_id))
            
            # Execute all cancellations in parallel
            results = await asyncio.gather(*cancel_tasks, return_exceptions=True)
            
            cancelled_orders = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logging.error(f"Failed to cancel order: {result}")
                else:
                    cancelled_orders.append(result)
                    
            logging.info(f"Cancelled {len(cancelled_orders)} out of {len(pending_orders)} pending orders")
            return cancelled_orders
            
        except Exception as e:
            logging.error(f"Error cancelling all pending orders: {e}")
            raise HTTPException(status_code=500, detail="Internal server error cancelling orders")

    async def _cancel_order_fast(self, order_id: int):
        """🚀 SPEED: Fast order cancellation helper for parallel processing"""
        try:
            result = await self.cancel_order(order_id)
            logging.info(f"Successfully cancelled order {order_id}")
            return result
        except HTTPException as e:
            if e.status_code == 404:
                logging.info(f"Order {order_id} already filled/cancelled (404)")
                return {"id": order_id, "status": "already_handled"}
            else:
                logging.error(f"Failed to cancel order {order_id}: {e.detail}")
                raise
        except Exception as e:
            logging.error(f"Failed to cancel order {order_id}: {e}")
            raise

    async def get_positions(self):
        """
        🚀 SPEED OPTIMIZED: Retrieves all open positions
        """
        if not self.access_token:
            await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        try:
            # 🚀 SPEED: Use persistent client
            client = await self._get_http_client()
            response = await client.get(f"{BASE_URL}/position/list", headers=headers)
            response.raise_for_status()
            positions = response.json()
            
            # 🔥 ENHANCED POSITION DEBUGGING: Log all position objects for analysis
            logging.info(f"🔍 RAW POSITIONS RESPONSE: {json.dumps(positions, indent=2)}")
            
            # Filter for open positions only (netPos != 0)
            open_positions = [pos for pos in positions if pos.get("netPos", 0) != 0]
            logging.info(f"Found {len(open_positions)} open positions")
            
            # 🔥 ENHANCED DEBUGGING: Log each open position structure
            for i, pos in enumerate(open_positions):
                logging.info(f"🔍 OPEN POSITION {i+1}: {json.dumps(pos, indent=2)}")
                # Log all available fields for debugging
                all_fields = list(pos.keys())
                logging.info(f"🔍 Available fields in position {i+1}: {all_fields}")
            
            return open_positions
                
        except httpx.HTTPStatusError as e:
            logging.error(f"Failed to get positions: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Failed to get positions: {e.response.text}")
        except Exception as e:
            logging.error(f"Unexpected error getting positions: {e}")
            raise HTTPException(status_code=500, detail="Internal server error getting positions")

    async def force_close_all_positions_immediately(self):
        """
        🚀 SPEED OPTIMIZED: Aggressively closes all positions and cancels all orders using parallel processing
        """
        if not self.access_token:
            await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        logging.info("🔥 Starting aggressive position and order cleanup")
        
        # 🚀 SPEED OPTIMIZATION: Parallel order cancellation and position closure
        try:
            # Step 1: Get pending orders and positions in parallel
            pending_orders_task = self.get_pending_orders()
            positions_task = self.get_positions()
            
            pending_orders, positions = await asyncio.gather(pending_orders_task, positions_task)
            
            # Step 2: Cancel all orders in parallel
            if pending_orders:
                cancel_tasks = []
                client = await self._get_http_client()
                
                for order in pending_orders:
                    order_id = order.get("id")
                    if order_id:
                        cancel_tasks.append(self._fast_cancel_order(client, order_id, headers))
                
                if cancel_tasks:
                    await asyncio.gather(*cancel_tasks, return_exceptions=True)
                    logging.info(f"✅ Processed {len(cancel_tasks)} order cancellations in parallel")
            
            # Step 3: Close all positions in parallel
            if positions:
                close_tasks = []
                client = await self._get_http_client()
                
                for position in positions:
                    net_pos = position.get("netPos", 0)
                    if net_pos != 0:
                        symbol = position.get("symbol") or str(position.get("contractId"))
                        if symbol:
                            close_tasks.append(self._fast_close_position(client, symbol, net_pos, headers))
                
                if close_tasks:
                    await asyncio.gather(*close_tasks, return_exceptions=True)
                    logging.info(f"✅ Processed {len(close_tasks)} position closures in parallel")
            
            # Step 4: Quick verification
            await asyncio.sleep(1)  # 🚀 SPEED: Reduced verification wait time
            remaining_positions = await self.get_positions()
            
            if remaining_positions:
                logging.error(f"❌ {len(remaining_positions)} positions still open after cleanup")
                return False
                
            logging.info("✅ All positions successfully closed with speed optimization")
            return True
            
        except Exception as e:
            logging.error(f"❌ Error during optimized position closure: {e}")
            return False

    async def _fast_cancel_order(self, client, order_id, headers):
        """🚀 SPEED: Fast order cancellation helper"""
        try:
            response = await client.post(f"{BASE_URL}/order/cancel/{order_id}", headers=headers)
            if response.status_code == 200:
                logging.info(f"✅ Cancelled order {order_id}")
            elif response.status_code == 404:
                logging.info(f"✅ Order {order_id} already filled/cancelled (404)")
            else:
                logging.warning(f"⚠️ Order {order_id} cancel response: {response.status_code}")
        except Exception as e:
            logging.error(f"❌ Failed to cancel order {order_id}: {e}")

    async def _fast_close_position(self, client, symbol, net_pos, headers):
        """🚀 SPEED: Fast position closure helper"""
        try:
            close_action = "Sell" if net_pos > 0 else "Buy"
            close_order = {
                "accountSpec": self.account_spec,
                "accountId": self.account_id,
                "action": close_action,
                "symbol": symbol,
                "orderQty": abs(net_pos),
                "orderType": "Market",
                "timeInForce": "IOC",  # 🚀 SPEED: Immediate or Cancel for fastest execution
                "isAutomated": True
            }

            response = await client.post(f"{BASE_URL}/order/placeorder", json=close_order, headers=headers)
            if response.status_code == 200:
                logging.info(f"✅ Fast closed position for {symbol}")
            else:
                logging.error(f"❌ Failed to close position for {symbol}: {response.status_code}")
        except Exception as e:
            logging.error(f"❌ Failed to close position for {symbol}: {e}")

    async def determine_optimal_order_type(self, symbol: str, action: str, target_price: float) -> dict:
        """
        🚀 SPEED OPTIMIZED: Intelligently determines whether to use Stop or Limit orders
        """
        try:
            # 🚀 SPEED: Immediate decision without market data lookup for fastest execution
            # Default to Stop orders for breakout strategies (fastest trigger execution)
            if action.lower() == "buy":
                return {
                    "orderType": "Stop",
                    "stopPrice": target_price
                }
            else:
                return {
                    "orderType": "Stop",
                    "stopPrice": target_price
                }
                
        except Exception as e:
            logging.error(f"Error in determine_optimal_order_type: {e}")
            # Fallback to Stop orders for speed
            return {
                "orderType": "Stop", 
                "stopPrice": target_price
            }
