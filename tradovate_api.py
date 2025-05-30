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
        🔥 ENHANCED CRITICAL FIX: Aggressively closes ALL positions and cancels ALL orders using multiple strategies.
        
        This method uses multiple approaches with fallbacks:
        1. Cancel ALL pending orders first
        2. Close all positions with Market orders using IOC execution
        3. Multiple retry attempts with different order types
        4. Position verification and cleanup
        
        Returns:
            bool: True if all positions successfully closed, False otherwise
        """
        if not self.access_token:
            await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        
        logging.info("🔥🔥🔥 STARTING AGGRESSIVE POSITION AND ORDER CLEANUP 🔥🔥🔥")
        
        # STEP 1: Cancel ALL pending orders first
        try:
            logging.info("🔥 STEP 1: Cancelling ALL pending orders")
            cancelled_orders = await self.cancel_all_pending_orders()
            logging.info(f"✅ Cancelled {len(cancelled_orders)} pending orders")
        except Exception as e:
            logging.error(f"❌ Failed to cancel all orders: {e}")
            # Continue anyway - we'll handle positions regardless
        
        max_attempts = 3
        
        for attempt in range(max_attempts):
            try:
                logging.info(f"🔥 Force position closure attempt {attempt + 1}/{max_attempts}")
                
                # Step 1: Get all open positions
                positions = await self.get_positions()
                if not positions:
                    logging.info("✅ No open positions found - all positions closed successfully")
                    return True
                
                logging.info(f"🔥 Found {len(positions)} open positions to close")                # Step 2: CLOSE ALL POSITIONS IMMEDIATELY (most aggressive method)
                for position in positions:
                    net_pos = position.get("netPos", 0)
                    
                    # 🔥 CRITICAL DEBUG: Log the full position object to understand structure
                    logging.info(f"🔍 POSITION OBJECT: {json.dumps(position, indent=2)}")
                    
                    if net_pos != 0:  # Close any position with non-zero netPos
                        logging.info(f"🔥 CLOSING position with netPos={net_pos}")
                        
                        # 🔥 ENHANCED SYMBOL DETECTION: Check all possible position fields
                        symbol_candidates = [
                            position.get("symbol"),
                            position.get("contractName"), 
                            position.get("instrument"),
                            position.get("masterInstrument", {}).get("symbol") if position.get("masterInstrument") else None,
                            position.get("contract", {}).get("symbol") if position.get("contract") else None,
                            position.get("contractId")
                        ]
                        
                        # Find the first valid symbol identifier
                        symbol = None
                        for candidate in symbol_candidates:
                            if candidate and str(candidate).strip():
                                symbol = str(candidate).strip()
                                logging.info(f"🔍 Found symbol identifier: {symbol}")
                                break
                                
                        # If still no symbol, try contractId-based approach
                        if not symbol:
                            contract_id = position.get("contractId")
                            if contract_id:
                                # Use contractId directly in orders - some endpoints support this
                                symbol = f"CONTRACT_{contract_id}"
                                logging.info(f"🔍 Using contractId fallback: {symbol}")
                        
                        # 🔥 METHOD 1: Try Tradovate's liquidateposition endpoint (most direct)
                        if symbol and not symbol.startswith("CONTRACT_"):
                            try:
                                liquidation_payload = {"symbol": symbol}
                                async with httpx.AsyncClient() as client:
                                    response = await client.post(f"{BASE_URL}/order/liquidateposition", 
                                                               json=liquidation_payload, headers=headers)
                                    if response.status_code == 200:
                                        result = response.json()
                                        logging.info(f"✅ LIQUIDATION order placed: {result}")
                                        continue  # Skip other methods if liquidation works
                                    else:
                                        logging.warning(f"⚠️ Liquidation failed, trying market orders")
                            except Exception as e1:
                                logging.warning(f"⚠️ Liquidation method failed: {e1}")
                        
                        # 🔥 METHOD 2: IOC Market order with enhanced identification
                        try:
                            close_action = "Sell" if net_pos > 0 else "Buy"
                            close_quantity = abs(net_pos)
                            
                            market_order = {
                                "accountSpec": self.account_spec,
                                "accountId": self.account_id,
                                "action": close_action,
                                "orderQty": close_quantity,
                                "orderType": "Market",
                                "timeInForce": "IOC",  # Immediate or Cancel for fastest execution
                                "isAutomated": True
                            }
                            
                            # 🔥 ENHANCED ORDER IDENTIFICATION: Try multiple field combinations
                            order_placed = False
                            
                            # Try 1: Use symbol if we found one
                            if symbol and not symbol.startswith("CONTRACT_"):
                                market_order["symbol"] = symbol
                                try:
                                    async with httpx.AsyncClient() as client:
                                        response = await client.post(f"{BASE_URL}/order/placeorder", 
                                                                   json=market_order, headers=headers)
                                        response.raise_for_status()
                                        result = response.json()
                                        logging.info(f"✅ IOC Market order (symbol) placed: {result}")
                                        order_placed = True
                                except Exception as e:
                                    logging.warning(f"⚠️ Market order with symbol failed: {e}")
                            
                            # Try 2: Use contractId directly if symbol method failed
                            if not order_placed and position.get("contractId"):
                                market_order_contractid = market_order.copy()
                                market_order_contractid.pop("symbol", None)  # Remove symbol field
                                market_order_contractid["contractId"] = position.get("contractId")
                                try:
                                    async with httpx.AsyncClient() as client:
                                        response = await client.post(f"{BASE_URL}/order/placeorder", 
                                                                   json=market_order_contractid, headers=headers)
                                        response.raise_for_status()
                                        result = response.json()
                                        logging.info(f"✅ IOC Market order (contractId) placed: {result}")
                                        order_placed = True
                                except Exception as e:
                                    logging.warning(f"⚠️ Market order with contractId failed: {e}")
                            
                            if not order_placed:
                                raise Exception("All market order identification methods failed")
                                    
                        except Exception as e2:
                            logging.error(f"❌ IOC Market order failed: {e2}")
                            
                            # 🔥 METHOD 3: Try position close API directly (if available)
                            try:
                                # Some APIs support closing by position ID
                                position_id = position.get("id") or position.get("positionId")
                                if position_id:
                                    close_payload = {"positionId": position_id}
                                    async with httpx.AsyncClient() as client:
                                        response = await client.post(f"{BASE_URL}/position/close", 
                                                                   json=close_payload, headers=headers)
                                        if response.status_code == 200:
                                            result = response.json()
                                            logging.info(f"✅ Position close API succeeded: {result}")
                                            continue
                                        else:
                                            logging.warning(f"Position close API failed: {response.status_code}")
                            except Exception as e3:
                                logging.error(f"❌ Position close API failed: {e3}")
                                
                                # 🔥 METHOD 4: Final attempt with relaxed constraints
                                logging.warning(f"⚠️ All standard methods failed for position: {position}")
                                logging.warning(f"⚠️ Position may require manual intervention or different API")
                    else:
                        logging.info(f"✅ Position already closed: netPos={net_pos}")
                
                # Step 3: Wait for orders to execute
                await asyncio.sleep(3)
                
                # Step 4: Verify all positions are closed
                final_positions = await self.get_positions()
                if not final_positions:
                    logging.info("✅ All positions successfully closed!")
                    return True
                else:
                    logging.warning(f"❌ {len(final_positions)} positions still open after attempt {attempt + 1}")
                    for pos in final_positions:
                        logging.warning(f"❌ Remaining: {pos.get('symbol')} netPos={pos.get('netPos', 0)}")
                      # If this is the last attempt, try the most aggressive approaches
                    if attempt == max_attempts - 1:
                        logging.info("🔥 FINAL ATTEMPT: Using most aggressive closure methods")
                        for pos in final_positions:
                            symbol = pos.get("symbol")
                            net_pos = pos.get("netPos", 0)
                            if symbol and net_pos != 0:
                                # Final attempt 1: Try liquidation again
                                try:
                                    result = await self.liquidate_position(symbol)
                                    logging.info(f"✅ Final liquidation attempt succeeded for {symbol}")
                                    continue
                                except Exception as e:
                                    logging.error(f"❌ Final liquidation attempt failed for {symbol}: {e}")
                                
                                # Final attempt 2: Try multiple order types as last resort
                                for order_type in ["Market", "Limit"]:
                                    try:
                                        close_action = "Sell" if net_pos > 0 else "Buy"
                                        close_order = {
                                            "accountSpec": self.account_spec,
                                            "accountId": self.account_id,
                                            "action": close_action,
                                            "symbol": symbol,
                                            "orderQty": abs(net_pos),
                                            "orderType": order_type,
                                            "timeInForce": "FOK",  # Fill or Kill
                                            "isAutomated": True
                                        }
                                        
                                        # For limit orders, use a price that's very likely to fill
                                        if order_type == "Limit":
                                            # Use a price that's very favorable for immediate execution
                                            # This is aggressive but necessary for position closure
                                            close_order["price"] = pos.get("lastPrice", 0) * (0.95 if close_action == "Sell" else 1.05)
                                        
                                        async with httpx.AsyncClient() as client:
                                            response = await client.post(f"{BASE_URL}/order/placeorder", json=close_order, headers=headers)
                                            response.raise_for_status()
                                            logging.info(f"✅ Final attempt {order_type} order placed for {symbol}")
                                            break  # If successful, don't try other order types
                                            
                                    except Exception as e:
                                        logging.error(f"❌ Final attempt {order_type} order failed for {symbol}: {e}")
                          # Final wait and check
                        await asyncio.sleep(5)
                        
            except Exception as e:
                logging.error(f"❌ Error during force position closure attempt {attempt + 1}: {e}")
                
        # Final verification after all attempts
        try:
            remaining_positions = await self.get_positions()
            if remaining_positions:
                logging.error(f"❌ CRITICAL: {len(remaining_positions)} positions still open after all attempts!")
                for pos in remaining_positions:
                    logging.error(f"❌ Remaining position: {pos.get('symbol')} netPos={pos.get('netPos', 0)}")
                return False
            else:
                logging.info("✅ All positions finally closed")
                return True
        except Exception as e:
            logging.error(f"❌ Error checking final positions: {e}")
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
