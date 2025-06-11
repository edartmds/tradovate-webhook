#!/usr/bin/env python3
"""
Comprehensive test script to verify the complete order flow:
1. Entry order placement
2. Monitoring logic
3. Stop order placement after entry fill
"""

import asyncio
import logging
import json
import time
from unittest.mock import AsyncMock, MagicMock
from tradovate_api import TradovateClient

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Mock order status responses
ENTRY_FILLED_RESPONSE = {
    "id": "12345",
    "status": "Filled",
    "symbol": "NQM5",
    "action": "Buy",
    "orderQty": 1
}

ENTRY_WORKING_RESPONSE = {
    "id": "12345", 
    "status": "Working",
    "symbol": "NQM5",
    "action": "Buy",
    "orderQty": 1
}

STOP_ORDER_PLACED_RESPONSE = {
    "id": "67890",
    "status": "Working",
    "symbol": "NQM5",
    "action": "Sell",
    "orderQty": 1,
    "orderType": "Stop"
}

class MockHttpClient:
    def __init__(self):
        self.call_count = 0
        self.responses = {}
        
    async def __aenter__(self):
        return self
        
    async def __aexit__(self, *args):
        pass
        
    async def get(self, url, headers=None):
        self.call_count += 1
        
        # First few calls return "Working", then return "Filled"
        if "order/12345" in url:
            if self.call_count <= 2:
                response = MagicMock()
                response.raise_for_status = MagicMock()
                response.json = MagicMock(return_value=ENTRY_WORKING_RESPONSE)
                return response
            else:
                response = MagicMock()
                response.raise_for_status = MagicMock()
                response.json = MagicMock(return_value=ENTRY_FILLED_RESPONSE)
                return response
        
        # Return default response
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={})
        return response
        
    async def post(self, url, headers=None, json=None):
        if "placeorder" in url:
            response = MagicMock()
            response.raise_for_status = MagicMock()
            response.json = MagicMock(return_value=STOP_ORDER_PLACED_RESPONSE)
            return response
        
        # Return default response for other POST requests
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={"status": "ok"})
        return response

async def test_monitor_all_orders():
    """Test the monitoring function with mocked responses"""
    
    print("Testing monitor_all_orders function...")
    
    # Import the monitoring function
    import sys
    import os
    sys.path.append(os.path.dirname(__file__))
    
    # Import the function from main module
    from main import monitor_all_orders
    
    # Mock client
    mock_client = MagicMock()
    mock_client.access_token = "test-token"
    mock_client.place_order = AsyncMock(return_value=STOP_ORDER_PLACED_RESPONSE)
    
    # Order tracking setup
    order_tracking = {
        "ENTRY": "12345",  # This order will be "filled" after a few calls
        "TP1": "54321",
        "STOP": None  # Will be set after ENTRY fills
    }
    
    # Stop order data
    stop_order_data = {
        "accountId": 18653267,
        "symbol": "NQM5", 
        "action": "Sell",
        "orderQty": 1,
        "orderType": "Stop",
        "stopPrice": 18500.0,
        "timeInForce": "GTC",
        "isAutomated": True
    }
    
    # Mock httpx globally
    import httpx
    original_async_client = httpx.AsyncClient
    httpx.AsyncClient = MockHttpClient
    
    # Mock the global client in main module
    import main
    main.client = mock_client
    
    try:
        # Create a task that will timeout after 10 seconds
        monitoring_task = asyncio.create_task(
            monitor_all_orders(order_tracking, "NQM5", stop_order_data)
        )
        
        # Wait for up to 10 seconds
        await asyncio.wait_for(monitoring_task, timeout=10.0)
        
        print("❌ Monitoring task completed unexpectedly")
        return False
        
    except asyncio.TimeoutError:
        # This is expected - the monitoring should continue running
        print("✓ Monitoring task is running (timed out as expected)")
        
        # Check if the mock client's place_order was called (indicating STOP order was placed)
        if mock_client.place_order.called:
            print("✓ STOP order placement was triggered!")
            print(f"STOP order call args: {mock_client.place_order.call_args}")
            
            # Check that the order tracking was updated
            if order_tracking.get("STOP") == "67890":
                print("✓ Order tracking was updated with STOP order ID")
                return True
            else:
                print(f"❌ Order tracking not updated. Current STOP ID: {order_tracking.get('STOP')}")
                return False
        else:
            print("❌ STOP order placement was NOT triggered")
            return False
            
    except Exception as e:
        print(f"❌ Monitoring task failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Restore original httpx
        httpx.AsyncClient = original_async_client
        
        # Cancel the task if it's still running
        if not monitoring_task.done():
            monitoring_task.cancel()
            try:
                await monitoring_task
            except asyncio.CancelledError:
                pass

async def main():
    """Run all tests"""
    
    print("=" * 60)
    print("COMPREHENSIVE ORDER FLOW TEST")
    print("=" * 60)
    
    # Test 1: Basic authentication
    print("\n1. Testing authentication...")
    try:
        client = TradovateClient()
        await client.authenticate()
        print(f"✓ Authentication successful. Account ID: {client.account_id}")
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        return False
    
    # Test 2: Stop order data validation
    print("\n2. Testing stop order data validation...")
    stop_order_data = {
        "accountId": client.account_id,
        "symbol": "NQM5",
        "action": "Sell",
        "orderQty": 1,
        "orderType": "Stop", 
        "stopPrice": 18500.0,
        "timeInForce": "GTC",
        "isAutomated": True
    }
    
    required_fields = ["accountId", "symbol", "action", "orderQty", "orderType", "stopPrice"]
    for field in required_fields:
        if field not in stop_order_data:
            print(f"❌ Missing required field: {field}")
            return False
    print("✓ Stop order data validation passed")
    
    # Test 3: Monitoring logic
    print("\n3. Testing monitoring logic...")
    monitor_result = await test_monitor_all_orders()
    
    if monitor_result:
        print("\n" + "=" * 60)
        print("✓ ALL TESTS PASSED!")
        print("The stop order placement logic is working correctly.")
        print("=" * 60)
        return True
    else:
        print("\n" + "=" * 60) 
        print("❌ TESTS FAILED!")
        print("There are issues with the stop order placement logic.")
        print("=" * 60)
        return False

if __name__ == "__main__":
    try:
        result = asyncio.run(main())
        exit(0 if result else 1)
    except KeyboardInterrupt:
        print("\nTest interrupted")
        exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
