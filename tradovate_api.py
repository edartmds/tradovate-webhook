import httpx
import os
import logging
import json
import asyncio
from dotenv import load_dotenv
from fastapi import HTTPException

load_dotenv()

# 🔴 LIVE TRADING MODE
BASE_URL = "https://live-api.tradovate.com/v1"

logging.info(f"🔴 LIVE MODE - Base URL: {BASE_URL}")


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
                    
                    # 🔍 ENHANCED ERROR CHECKING: Check what keys are in the response
                    available_keys = list(data.keys()) if isinstance(data, dict) else []
                    logging.info(f"🔍 Available keys in auth response: {available_keys}")
                    
                    # Try different possible token field names
                    if "accessToken" in data:
                        self.access_token = data["accessToken"]
                        logging.info("✅ Found accessToken in response")
                    elif "access_token" in data:
                        self.access_token = data["access_token"]
                        logging.info("✅ Found access_token in response")
                    elif "token" in data:
                        self.access_token = data["token"]
                        logging.info("✅ Found token in response")
                    else:
                        # Check if there's an error message in the response
                        if "errorText" in data:
                            error_msg = data["errorText"]
                            logging.error(f"❌ Tradovate API Error: {error_msg}")
                            raise HTTPException(status_code=401, detail=f"Authentication failed: {error_msg}")
                        elif "error" in data:
                            error_msg = data["error"]
                            logging.error(f"❌ Tradovate API Error: {error_msg}")
                            raise HTTPException(status_code=401, detail=f"Authentication failed: {error_msg}")
                        else:
                            logging.error(f"❌ No access token found in response. Available keys: {available_keys}")
                            raise KeyError(f"Access token not found in response. Available keys: {available_keys}")


                    # Fetch account ID
                    headers = {"Authorization": f"Bearer {self.access_token}"}
                    acc_res = await client.get(f"{BASE_URL}/account/list", headers=headers)
                    acc_res.raise_for_status()
                    account_data = acc_res.json()
                    logging.info(f"Account list response: {json.dumps(account_data, indent=2)}")
                    self.account_id = account_data[0]["id"]
                    self.account_spec = account_data[0].get("name")


                    # Use hardcoded values from .env if available (only if they're valid)
                    env_account_id = os.getenv("TRADOVATE_ACCOUNT_ID", "")
                    env_account_spec = os.getenv("TRADOVATE_ACCOUNT_SPEC", "")
                    
                    # Only use env values if they're not placeholders
                    if env_account_id and not env_account_id.startswith("WILL_BE_") and env_account_id.isdigit():
                        self.account_id = int(env_account_id)
                        logging.info(f"Using account_id from .env: {self.account_id}")
                    
                    if env_account_spec and not env_account_spec.startswith("WILL_BE_"):
                        self.account_spec = env_account_spec
                        logging.info(f"Using account_spec from .env: {self.account_spec}")
                    else:
                        logging.info(f"Using account_spec from API response: {self.account_spec}")


                    logging.info(f"Final account_id: {self.account_id}, account_spec: {self.account_spec}")


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
        Cancels a specific order by ID.        Args:
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
                    except HTTPException as e:
                        # If it's a 404, the order is already handled (filled/cancelled)
                        if e.status_code == 404:
                            logging.info(f"Order {order_id} already filled/cancelled (404)")
                            cancelled_orders.append({"id": order_id, "status": "already_handled"})
                        else:
                            logging.error(f"Failed to cancel order {order_id}: {e.detail}")
                    except Exception as e:
                        logging.error(f"Failed to cancel order {order_id}: {e}")
                       
            logging.info(f"Cancelled {len(cancelled_orders)} out of {len(pending_orders)} pending orders")
            return cancelled_orders
           
        except Exception as e:
            logging.error(f"Error cancelling all pending orders: {e}")
            raise HTTPException(status_code=500, detail="Internal server error cancelling orders")


    async def place_oco_order(self, order1: dict, order2: dict):
        """
        Places an Order Cancels Order (OCO) order on Tradovate.
       
        Args:
            order1 (dict): First order payload
            order2 (dict): Second order payload
           
        Returns:
            dict: The response from the Tradovate API.
        """
        if not self.access_token:
            await self.authenticate()


        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }


        # OCO requires a different format - orders as an array
        oco_payload = {
            "orders": [order1, order2]
        }


        try:
            async with httpx.AsyncClient() as client:
                logging.debug(f"Sending OCO order payload: {json.dumps(oco_payload, indent=2)}")
                response = await client.post(f"{BASE_URL}/order/placeoco", json=oco_payload, headers=headers)
                response.raise_for_status()
                response_data = response.json()
                logging.info(f"OCO order response: {json.dumps(response_data, indent=2)}")
                return response_data
        except httpx.HTTPStatusError as e:
            logging.error(f"OCO order placement failed: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"OCO order placement failed: {e.response.text}")
        except Exception as e:
            logging.error(f"Unexpected error during OCO order placement: {e}")
            raise HTTPException(status_code=500, detail="Internal server error during OCO order placement")


    async def get_positions(self):
        """
        Retrieves all open positions for the authenticated account.


        Returns:
            list: List of open positions.
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


    async def close_position(self, symbol: str):
        """
        Closes a specific position by symbol using a market order.


        Args:
            symbol (str): The symbol of the position to close.


        Returns:
            dict: The response from the Tradovate API.
        """
        if not self.access_token:
            await self.authenticate()


        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }


        # First, get the current position for this symbol
        try:
            positions = await self.get_positions()
            target_position = None
           
            for position in positions:
                if position.get("symbol") == symbol:
                    target_position = position
                    break
           
            if not target_position:
                logging.info(f"No open position found for symbol {symbol}")
                return {"status": "no_position", "message": f"No open position for {symbol}"}
           
            net_pos = target_position.get("netPos", 0)
            if net_pos == 0:
                logging.info(f"Position for {symbol} already closed (netPos = 0)")
                return {"status": "already_closed", "message": f"Position for {symbol} already closed"}
           
            # Determine the action needed to close the position
            # If netPos > 0 (long position), we need to sell to close
            # If netPos < 0 (short position), we need to buy to close
            close_action = "Sell" if net_pos > 0 else "Buy"
            close_quantity = abs(net_pos)
           
            logging.info(f"Closing position for {symbol}: netPos={net_pos}, action={close_action}, qty={close_quantity}")
           
            # Create market order to close position
            close_order = {
                "accountSpec": self.account_spec,
                "accountId": self.account_id,
                "action": close_action,
                "symbol": symbol,
                "orderQty": close_quantity,
                "orderType": "Market",
                "timeInForce": "GTC",
                "isAutomated": True
            }
           
            # Place the closing order
            async with httpx.AsyncClient() as client:
                logging.debug(f"Placing position close order: {json.dumps(close_order, indent=2)}")
                response = await client.post(f"{BASE_URL}/order/placeorder", json=close_order, headers=headers)
                response.raise_for_status()
                response_data = response.json()
                logging.info(f"Position close order placed: {json.dumps(response_data, indent=2)}")
                return response_data
               
        except httpx.HTTPStatusError as e:
            logging.error(f"Failed to close position for {symbol}: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Failed to close position: {e.response.text}")
        except Exception as e:
            logging.error(f"Unexpected error closing position for {symbol}: {e}")
            raise HTTPException(status_code=500, detail="Internal server error closing position")


    async def close_all_positions(self):
        """
        Closes all open positions for the authenticated account.


        Returns:
            list: List of close order responses.
        """
        try:
            positions = await self.get_positions()
            closed_positions = []
           
            for position in positions:
                symbol = position.get("symbol")
                net_pos = position.get("netPos", 0)
               
                if symbol and net_pos != 0:
                    try:
                        result = await self.close_position(symbol)
                        closed_positions.append(result)
                        logging.info(f"Successfully closed position for {symbol}")
                    except Exception as e:
                        logging.error(f"Failed to close position for {symbol}: {e}")
                       
            logging.info(f"Closed {len(closed_positions)} positions")
            return closed_positions
           
        except Exception as e:
            logging.error(f"Error closing all positions: {e}")
            raise HTTPException(status_code=500, detail="Internal server error closing positions")


    async def force_close_all_positions_immediately(self):
        """
        Aggressively closes all positions and cancels all orders, including take profit limit orders, using multiple strategies.
        """
        if not self.access_token:
            await self.authenticate()


        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }


        logging.info("🔥 Starting aggressive position and order cleanup")        # Step 1: Cancel all pending orders, including take profit limit orders
        try:
            pending_orders = await self.get_pending_orders()
            cancelled_orders = []
           
            for order in pending_orders:
                order_id = order.get("id")
                if order_id:
                    try:
                        async with httpx.AsyncClient() as client:
                            response = await client.post(f"{BASE_URL}/order/cancel/{order_id}", headers=headers)
                            response.raise_for_status()
                            cancelled_orders.append(order_id)
                            logging.info(f"✅ Cancelled order {order_id}")
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code == 404:
                            logging.info(f"✅ Order {order_id} already filled/cancelled (404)")
                            cancelled_orders.append(order_id)
                        else:
                            logging.error(f"❌ Failed to cancel order {order_id}: {e}")
                    except Exception as e:
                        logging.error(f"❌ Failed to cancel order {order_id}: {e}")


            logging.info(f"✅ Cancelled {len(cancelled_orders)} pending orders, including take profit limit orders")
        except Exception as e:
            logging.error(f"❌ Failed to cancel all orders: {e}")


        max_attempts = 3


        for attempt in range(max_attempts):
            try:
                logging.info(f"🔥 Attempt {attempt + 1}/{max_attempts} to close all positions")


                positions = await self.get_positions()
                if not positions:
                    logging.info("✅ No open positions found")
                    return True


                for position in positions:
                    net_pos = position.get("netPos", 0)
                    if net_pos == 0:
                        continue


                    symbol = position.get("symbol") or str(position.get("contractId"))
                    if not symbol:
                        logging.error(f"❌ Could not identify symbol for position: {position}")
                        continue


                    try:
                        close_action = "Sell" if net_pos > 0 else "Buy"
                        close_order = {
                            "accountSpec": self.account_spec,
                            "accountId": self.account_id,
                            "action": close_action,
                            "symbol": symbol,
                            "orderQty": abs(net_pos),
                            "orderType": "Market",
                            "timeInForce": "IOC",
                            "isAutomated": True
                        }


                        async with httpx.AsyncClient() as client:
                            response = await client.post(f"{BASE_URL}/order/placeorder", json=close_order, headers=headers)
                            response.raise_for_status()
                            logging.info(f"✅ Closed position for {symbol}")
                    except Exception as e:
                        logging.error(f"❌ Failed to close position for {symbol}: {e}")


                await asyncio.sleep(2)


            except Exception as e:
                logging.error(f"❌ Error during position closure attempt {attempt + 1}: {e}")


        # Final verification
        try:
            remaining_positions = await self.get_positions()
            if remaining_positions:
                logging.error(f"❌ {len(remaining_positions)} positions still open after all attempts")
                return False
            logging.info("✅ All positions successfully closed")
            return True
        except Exception as e:
            logging.error(f"❌ Error verifying positions: {e}")
            return False


    async def liquidate_position(self, symbol: str):
        """
        🔥 CRITICAL: Liquidates a specific position using the official Tradovate liquidation endpoint.
        This is the most aggressive way to close a position immediately.


        Args:
            symbol (str): The symbol of the position to liquidate.


        Returns:
            dict: The response from the Tradovate API.
        """
        if not self.access_token:
            await self.authenticate()


        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }


        # First, get the current position for this symbol
        try:
            positions = await self.get_positions()
            target_position = None
           
            for position in positions:
                if position.get("symbol") == symbol:
                    target_position = position
                    break
           
            if not target_position:
                logging.info(f"No open position found for symbol {symbol}")
                return {"status": "no_position", "message": f"No open position for {symbol}"}
           
            net_pos = target_position.get("netPos", 0)
            if net_pos == 0:
                logging.info(f"Position for {symbol} already closed (netPos = 0)")
                return {"status": "already_closed", "message": f"Position for {symbol} already closed"}
           
            logging.info(f"🔥 LIQUIDATING position for {symbol}: netPos={net_pos}")
           
            # Use the official liquidation endpoint
            liquidation_payload = {
                "symbol": symbol
            }
           
            # Place the liquidation order
            async with httpx.AsyncClient() as client:
                logging.debug(f"Placing liquidation order: {json.dumps(liquidation_payload, indent=2)}")
                response = await client.post(f"{BASE_URL}/order/liquidateposition", json=liquidation_payload, headers=headers)
                response.raise_for_status()
                response_data = response.json()
                logging.info(f"✅ Position liquidation order placed: {json.dumps(response_data, indent=2)}")
                return response_data
               
        except httpx.HTTPStatusError as e:
            logging.error(f"Failed to liquidate position for {symbol}: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Failed to liquidate position: {e.response.text}")
        except Exception as e:
            logging.error(f"Unexpected error liquidating position for {symbol}: {e}")
            raise HTTPException(status_code=500, detail="Internal server error liquidating position")


    async def liquidate_all_positions(self):
        """
        🔥 CRITICAL: Liquidates ALL open positions using the official Tradovate liquidation endpoint.
        This is the most aggressive way to close all positions immediately.


        Returns:
            list: List of liquidation responses.
        """
        try:
            positions = await self.get_positions()
            liquidated_positions = []
           
            for position in positions:
                symbol = position.get("symbol")
                net_pos = position.get("netPos", 0)
               
                if symbol and net_pos != 0:
                    try:
                        result = await self.liquidate_position(symbol)
                        liquidated_positions.append(result)
                        logging.info(f"✅ Successfully liquidated position for {symbol}")
                    except Exception as e:
                        logging.error(f"❌ Failed to liquidate position for {symbol}: {e}")
                       
            logging.info(f"🔥 Liquidated {len(liquidated_positions)} positions")
            return liquidated_positions
           
        except Exception as e:
            logging.error(f"Error liquidating all positions: {e}")
            raise HTTPException(status_code=500, detail="Internal server error liquidating positions")


    async def determine_optimal_order_type(self, symbol: str, action: str, target_price: float) -> dict:
        """
        Intelligently determines whether to use Stop or Limit orders based on market conditions.
       
        Args:
            symbol (str): Trading symbol
            action (str): "Buy" or "Sell"
            target_price (float): The target entry price
           
        Returns:
            dict: Order configuration with orderType, price/stopPrice
        """
        try:
            # For now, use a simple fallback strategy
            # This can be enhanced with real market data in the future
           
            # Default to Stop orders for breakout strategies
            if action.lower() == "buy":
                # For BUY orders, use Stop order (breakout above current price)
                return {
                    "orderType": "Stop",
                    "stopPrice": target_price
                }
            else:
                # For SELL orders, use Stop order (breakdown below current price)  
                return {
                    "orderType": "Stop",
                    "stopPrice": target_price
                }
               
        except Exception as e:
            logging.error(f"Error in determine_optimal_order_type: {e}")
            # Fallback to Stop orders
            return {
                "orderType": "Stop",
                "stopPrice": target_price
            }

