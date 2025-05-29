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
                
                # Filter for open positions only (netPos != 0)
                open_positions = [pos for pos in positions if pos.get("netPos", 0) != 0]
                logging.info(f"Found {len(open_positions)} open positions")
                logging.debug(f"Open positions: {json.dumps(open_positions, indent=2)}")
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
        Waits for positions to be actually closed before returning.

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
                        logging.info(f"Successfully initiated position close for {symbol}")
                    except Exception as e:
                        logging.error(f"Failed to close position for {symbol}: {e}")
            
            # Wait for all positions to be actually closed
            if closed_positions:
                logging.info("Waiting for positions to be closed...")
                await self._wait_for_positions_closed([pos.get("symbol") for pos in positions if pos.get("netPos", 0) != 0])
                        
            logging.info(f"Closed {len(closed_positions)} positions")
            return closed_positions
            
        except Exception as e:
            logging.error(f"Error closing all positions: {e}")
            raise HTTPException(status_code=500, detail="Internal server error closing positions")

    async def _wait_for_positions_closed(self, symbols: list, max_wait_seconds: int = 30):
        """
        Waits for positions to be closed by checking position status.
        
        Args:
            symbols (list): List of symbols to check for position closure
            max_wait_seconds (int): Maximum time to wait for positions to close
        """
        start_time = asyncio.get_event_loop().time()
        wait_interval = 1.0  # Check every 1 second
        
        while (asyncio.get_event_loop().time() - start_time) < max_wait_seconds:
            try:
                current_positions = await self.get_positions()
                open_symbols = []
                
                for position in current_positions:
                    symbol = position.get("symbol")
                    net_pos = position.get("netPos", 0)
                    if symbol in symbols and net_pos != 0:
                        open_symbols.append(symbol)
                
                if not open_symbols:
                    logging.info("All positions successfully closed")
                    return
                
                logging.info(f"Still waiting for positions to close: {open_symbols}")
                await asyncio.sleep(wait_interval)
                
            except Exception as e:
                logging.warning(f"Error checking position status during wait: {e}")
                await asyncio.sleep(wait_interval)
        
        # Final check after timeout
        try:
            final_positions = await self.get_positions()
            remaining_open = []
            for position in final_positions:
                symbol = position.get("symbol")
                net_pos = position.get("netPos", 0)
                if symbol in symbols and net_pos != 0:
                    remaining_open.append(f"{symbol} (netPos: {net_pos})")
            
            if remaining_open:
                logging.warning(f"Timeout waiting for positions to close. Still open: {remaining_open}")
            else:
                logging.info("All positions confirmed closed after timeout period")
                
        except Exception as e:
            logging.error(f"Error in final position check: {e}")

    async def force_close_all_positions_immediately(self):
        """
        Aggressively closes all positions using immediate market orders.
        This method uses a more direct approach for immediate position closure.
        
        Returns:
            list: List of closed position results
        """
        if not self.access_token:
            await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        try:
            # Get all current positions
            positions = await self.get_positions()
            closed_results = []
            
            for position in positions:
                symbol = position.get("symbol")
                net_pos = position.get("netPos", 0)
                
                if symbol and net_pos != 0:
                    logging.info(f"🔥 FORCE CLOSING POSITION: {symbol} (netPos: {net_pos})")
                    
                    # Determine close action and quantity
                    close_action = "Sell" if net_pos > 0 else "Buy"
                    close_qty = abs(net_pos)
                    
                    # Create immediate market order
                    market_close_order = {
                        "accountSpec": self.account_spec,
                        "accountId": self.account_id,
                        "action": close_action,
                        "symbol": symbol,
                        "orderQty": close_qty,
                        "orderType": "Market",
                        "timeInForce": "IOC",  # Immediate or Cancel for faster execution
                        "isAutomated": True
                    }
                    
                    try:
                        async with httpx.AsyncClient(timeout=10.0) as client:
                            logging.info(f"Placing immediate market close order: {json.dumps(market_close_order, indent=2)}")
                            response = await client.post(f"{BASE_URL}/order/placeorder", json=market_close_order, headers=headers)
                            response.raise_for_status()
                            result = response.json()
                            closed_results.append(result)
                            logging.info(f"✅ Force close order placed for {symbol}: {result}")
                            
                    except Exception as e:
                        logging.error(f"❌ Failed to force close {symbol}: {e}")
                        
                        # Try alternative approach - liquidate position directly
                        try:
                            liquidate_payload = {
                                "accountId": self.account_id,
                                "symbol": symbol
                            }
                            
                            liquidate_response = await client.post(f"{BASE_URL}/position/liquidateposition", json=liquidate_payload, headers=headers)
                            if liquidate_response.status_code == 200:
                                liquidate_result = liquidate_response.json()
                                closed_results.append(liquidate_result)
                                logging.info(f"✅ Position liquidated for {symbol}: {liquidate_result}")
                            else:
                                logging.error(f"❌ Liquidation also failed for {symbol}: {liquidate_response.text}")
                        except Exception as liquidate_error:
                            logging.error(f"❌ Liquidation attempt failed for {symbol}: {liquidate_error}")
            
            # Brief wait for orders to process
            if closed_results:
                await asyncio.sleep(2)
                
                # Verify closure
                final_positions = await self.get_positions()
                remaining = [pos for pos in final_positions if pos.get("netPos", 0) != 0]
                
                if remaining:
                    logging.warning(f"⚠️ {len(remaining)} positions still open after force close attempt")
                    for pos in remaining:
                        logging.warning(f"  - {pos.get('symbol')}: netPos={pos.get('netPos', 0)}")
                else:
                    logging.info("✅ All positions successfully force closed")
            
            return closed_results
            
        except Exception as e:
            logging.error(f"❌ Error in force_close_all_positions_immediately: {e}")
            raise HTTPException(status_code=500, detail=f"Force position closure failed: {str(e)}")
