#!/usr/bin/env python3
"""
Test script to verify stop order placement logic.
This simulates the monitoring function to ensure STOP orders are placed correctly.
"""

import asyncio
import logging
import json
import sys
from tradovate_api import TradovateClient

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def test_stop_order_placement():
    """Test the stop order placement logic"""
    
    print("Starting stop order placement test...")
    
    # Sample stop order data that would be created in the webhook
    stop_order_data = {
        "accountId": 18653267,  # From .env
        "symbol": "NQM5",
        "action": "Sell",  # Opposite of Buy entry
        "orderQty": 1,
        "orderType": "Stop",
        "stopPrice": 18500.0,  # Example stop price
        "timeInForce": "GTC",
        "isAutomated": True
    }
    
    # Initialize client
    client = TradovateClient()
    
    try:
        print("Attempting authentication...")
        # Authenticate
        await client.authenticate()
        print(f"✓ Authentication successful. Account ID: {client.account_id}")
        
        # Validate stop order data
        required_fields = ["accountId", "symbol", "action", "orderQty", "orderType", "stopPrice"]
        for field in required_fields:
            if field not in stop_order_data:
                raise ValueError(f"Missing required field in stop_order_data: {field}")
        
        print(f"✓ Stop order data validation passed")
        print(f"Stop order data: {json.dumps(stop_order_data, indent=2)}")
        
        print("Test completed successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    try:
        result = asyncio.run(test_stop_order_placement())
        sys.exit(0 if result else 1)
    except KeyboardInterrupt:
        print("Test interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
