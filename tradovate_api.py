import httpx
import os
import logging
import json  # Added for pretty-printing JSON responses
import asyncio  # Added for retry logic
import httpx  # Added for HTTP requests
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
        max_retries = 5
        backoff_factor = 2

        for attempt in range(max_retries):
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

    async def place_stop_order(self, entry_order_id: int, stop_price: float):
        """
        Places a STOP order after the ENTRY order is filled.

        Args:
            entry_order_id (int): The ID of the ENTRY order.
            stop_price (float): The price for the STOP order.

        Returns:
            dict: The response from the Tradovate API.
        """
        if not self.access_token:
            await self.authenticate()

        if not entry_order_id:
            logging.error("Invalid ENTRY order ID. Cannot place STOP order.")
            raise HTTPException(status_code=400, detail="Invalid ENTRY order ID")

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        stop_order_payload = {
            "accountId": self.account_id,
            "action": "Sell",  # Assuming STOP orders are for selling
            "linkedOrderId": entry_order_id,
            "orderType": "stop",
            "price": stop_price,
            "timeInForce": "GTC",
            "isAutomated": True
        }

        try:
            async with httpx.AsyncClient() as client:
                logging.debug(f"Sending STOP order payload: {json.dumps(stop_order_payload, indent=2)}")
                response = await client.post(f"{BASE_URL}/order/placeorder", json=stop_order_payload, headers=headers)
                response.raise_for_status()
                response_data = response.json()
                logging.info(f"STOP order response: {json.dumps(response_data, indent=2)}")
                return response_data
        except httpx.HTTPStatusError as e:
            logging.error(f"STOP order placement failed: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"STOP order placement failed: {e.response.text}")
        except Exception as e:
            logging.error(f"Unexpected error during STOP order placement: {e}")
            raise HTTPException(status_code=500, detail="Internal server error during STOP order placement")

    async def get_pending_orders(self):
        """
        Retrieves all pending orders for the authenticated account.

        Returns:
            list: List of pending orders.
        """
        if not self.access_token:
            await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        try:
            async with httpx.AsyncClient() as client:
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
        Cancels a specific order by ID.

        Args:
            order_id (int): The ID of the order to cancel.

        Returns:
            dict: The response from the Tradovate API.
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
            async with httpx.AsyncClient() as client:
                logging.debug(f"Canceling order {order_id}")
                response = await client.post(f"{BASE_URL}/order/cancelorder", json=cancel_payload, headers=headers)
                response.raise_for_status()
                response_data = response.json()
                logging.info(f"Order {order_id} cancelled successfully: {json.dumps(response_data, indent=2)}")
                return response_data
                
        except httpx.HTTPStatusError as e:
            logging.error(f"Failed to cancel order {order_id}: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Failed to cancel order: {e.response.text}")
        except Exception as e:
            logging.error(f"Unexpected error canceling order {order_id}: {e}")
            raise HTTPException(status_code=500, detail="Internal server error canceling order")

    async def cancel_all_pending_orders(self):
        """
        Cancels all pending orders for the authenticated account.

        Returns:
            list: List of cancelled order responses.
        """
        try:
            pending_orders = await self.get_pending_orders()
            cancelled_orders = []
            
            for order in pending_orders:
                order_id = order.get("id")
                if order_id:
                    try:
                        result = await self.cancel_order(order_id)
                        cancelled_orders.append(result)
                        logging.info(f"Successfully cancelled order {order_id}")
                    except Exception as e:
                        logging.error(f"Failed to cancel order {order_id}: {e}")
                        
            logging.info(f"Cancelled {len(cancelled_orders)} out of {len(pending_orders)} pending orders")
            return cancelled_orders
            
        except Exception as e:
            logging.error(f"Error cancelling all pending orders: {e}")
            raise HTTPException(status_code=500, detail="Internal server error cancelling orders")

    async def get_all_positions(self):
        """
        Retrieves all positions for the authenticated account.

        Returns:
            list: List of positions.
        """
        if not self.access_token:
            await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{BASE_URL}/position/list", headers=headers)
                response.raise_for_status()
                positions = response.json()
                
                # Filter for non-zero positions
                open_positions = [pos for pos in positions if pos.get("netPos", 0) != 0]
                logging.info(f"🔍 Found {len(open_positions)} open positions")
                
                for pos in open_positions:
                    symbol = pos.get("symbol", "Unknown")
                    net_pos = pos.get("netPos", 0)
                    logging.info(f"📊 Position: {symbol} - Net: {net_pos}")
                
                return open_positions
                
        except httpx.HTTPStatusError as e:
            logging.error(f"Failed to get positions: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Failed to get positions: {e.response.text}")
        except Exception as e:
            logging.error(f"Unexpected error getting positions: {e}")
            raise HTTPException(status_code=500, detail="Internal server error getting positions")

    async def close_position_at_market(self, symbol: str, quantity: int):
        """
        Closes a position at market price immediately.

        Args:
            symbol (str): The symbol to close
            quantity (int): The quantity to close (positive for long, negative for short)

        Returns:
            dict: The response from the Tradovate API.
        """
        if not self.access_token:
            await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        # Determine the action to close the position
        if quantity > 0:
            action = "Sell"  # Close long position
        else:
            action = "Buy"   # Close short position
            quantity = abs(quantity)  # Make quantity positive

        market_order_payload = {
            "accountSpec": self.account_spec,
            "accountId": self.account_id,
            "action": action,
            "symbol": symbol,
            "orderQty": quantity,
            "orderType": "Market",
            "timeInForce": "IOC",  # Immediate or Cancel
            "isAutomated": True
        }

        try:
            async with httpx.AsyncClient() as client:
                logging.info(f"🔥 Placing market order to close position: {symbol} - {action} {quantity}")
                logging.debug(f"Market order payload: {json.dumps(market_order_payload, indent=2)}")
                response = await client.post(f"{BASE_URL}/order/placeorder", json=market_order_payload, headers=headers)
                response.raise_for_status()
                response_data = response.json()
                logging.info(f"✅ Market order placed successfully: {json.dumps(response_data, indent=2)}")
                return response_data
                
        except httpx.HTTPStatusError as e:
            logging.error(f"❌ Market order placement failed: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Market order placement failed: {e.response.text}")
        except Exception as e:
            logging.error(f"❌ Unexpected error during market order placement: {e}")
            raise HTTPException(status_code=500, detail="Internal server error during market order placement")

    async def close_all_positions(self):
        """
        Closes all open positions at market price.

        Returns:
            list: List of market order responses.
        """
        logging.info("🔥 STARTING AGGRESSIVE POSITION CLOSURE")
        
        try:
            positions = await self.get_all_positions()
            closed_orders = []
            
            if not positions:
                logging.info("✅ No open positions to close")
                return closed_orders
            
            for position in positions:
                symbol = position.get("symbol")
                net_pos = position.get("netPos", 0)
                
                if net_pos != 0:
                    logging.info(f"🔥 Closing position: {symbol} - Net: {net_pos}")
                    try:
                        result = await self.close_position_at_market(symbol, net_pos)
                        closed_orders.append(result)
                        logging.info(f"✅ Successfully placed close order for {symbol}")
                        
                        # Wait for market order execution
                        await asyncio.sleep(3)
                        
                    except Exception as e:
                        logging.error(f"❌ Failed to close position {symbol}: {e}")
            
            # Verify positions are closed
            await asyncio.sleep(2)
            remaining_positions = await self.get_all_positions()
            
            if remaining_positions:
                logging.warning(f"⚠️ Still have {len(remaining_positions)} open positions after closure attempts")
                for pos in remaining_positions:
                    symbol = pos.get("symbol", "Unknown")
                    net_pos = pos.get("netPos", 0)
                    logging.warning(f"⚠️ Remaining position: {symbol} - Net: {net_pos}")
            else:
                logging.info("✅ All positions successfully closed")
                        
            logging.info(f"Closed {len(closed_orders)} positions")
            return closed_orders
            
        except Exception as e:
            logging.error(f"❌ Error closing all positions: {e}")
            raise HTTPException(status_code=500, detail="Internal server error closing positions")

    async def force_close_all_positions_immediately(self):
        """
        Aggressively closes all positions using multiple methods as fallbacks.

        Returns:
            bool: True if all positions were closed successfully.
        """
        logging.info("🔥🔥🔥 FORCE CLOSING ALL POSITIONS IMMEDIATELY")
        
        try:
            # Method 1: Standard position closure
            logging.info("🔥 Method 1: Standard market orders")
            await self.close_all_positions()
            
            # Wait and verify
            await asyncio.sleep(3)
            positions = await self.get_all_positions()
            
            if not positions:
                logging.info("✅ All positions closed via standard method")
                return True
            
            # Method 2: Position liquidation API
            logging.info("🔥 Method 2: Position liquidation API")
            if not self.access_token:
                await self.authenticate()

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }
            
            for position in positions:
                symbol = position.get("symbol")
                try:
                    async with httpx.AsyncClient() as client:
                        liquidate_payload = {"symbol": symbol}
                        response = await client.post(f"{BASE_URL}/position/closeposition", 
                                                   json=liquidate_payload, headers=headers)
                        response.raise_for_status()
                        logging.info(f"✅ Liquidated position via API: {symbol}")
                except Exception as e:
                    logging.error(f"❌ Failed to liquidate {symbol} via API: {e}")
            
            # Final verification
            await asyncio.sleep(3)
            final_positions = await self.get_all_positions()
            
            if not final_positions:
                logging.info("✅ All positions closed via liquidation API")
                return True
            else:
                logging.error(f"❌ CRITICAL: Still have {len(final_positions)} positions after aggressive closure")
                for pos in final_positions:
                    symbol = pos.get("symbol", "Unknown")
                    net_pos = pos.get("netPos", 0)
                    logging.error(f"❌ REMAINING: {symbol} - Net: {net_pos}")
                return False
                
        except Exception as e:
            logging.error(f"❌ CRITICAL ERROR in force close: {e}")
            return False
