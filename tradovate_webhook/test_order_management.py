#!/usr/bin/env python3
"""
Test script for order management functionality in the Tradovate webhook.
This script tests the order cancellation and OCO/OSO placement logic.
"""

import asyncio
import json
import logging
from tradovate_api import TradovateClient

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def test_order_management():
    """Test order management functionality"""
    client = TradovateClient()
    
    try:
        # Step 1: Authenticate
        logging.info("=== TESTING AUTHENTICATION ===")
        await client.authenticate()
        logging.info(f"✅ Authentication successful")
        logging.info(f"Account ID: {client.account_id}")
        logging.info(f"Account Spec: {client.account_spec}")
        
        # Step 2: Get current pending orders
        logging.info("\n=== TESTING GET PENDING ORDERS ===")
        pending_orders = await client.get_pending_orders()
        logging.info(f"✅ Found {len(pending_orders)} pending orders")
        
        if pending_orders:
            logging.info("Current pending orders:")
            for order in pending_orders:
                logging.info(f"  - Order ID: {order.get('id')}, Symbol: {order.get('symbol')}, "
                           f"Action: {order.get('action')}, Type: {order.get('orderType')}, "
                           f"Status: {order.get('ordStatus')}")
        
        # Step 3: Test order cancellation (if any orders exist)
        if pending_orders:
            logging.info("\n=== TESTING ORDER CANCELLATION ===")
            first_order_id = pending_orders[0].get('id')
            
            # Test single order cancellation
            logging.info(f"Testing cancellation of order {first_order_id}")
            try:
                result = await client.cancel_order(first_order_id)
                logging.info(f"✅ Successfully cancelled order {first_order_id}")
            except Exception as e:
                logging.warning(f"⚠️ Failed to cancel order {first_order_id}: {e}")
            
            # Test cancel all remaining orders
            if len(pending_orders) > 1:
                logging.info("Testing cancel all pending orders")
                try:
                    cancelled_orders = await client.cancel_all_pending_orders()
                    logging.info(f"✅ Successfully cancelled {len(cancelled_orders)} orders")
                except Exception as e:
                    logging.warning(f"⚠️ Failed to cancel all orders: {e}")
        else:
            logging.info("ℹ️ No pending orders to test cancellation")
        
        # Step 4: Test OCO order placement
        logging.info("\n=== TESTING OCO ORDER PLACEMENT ===")
        
        # Sample OCO orders (entry stop + take profit limit)
        entry_order = {
            "accountSpec": client.account_spec,
            "accountId": client.account_id,
            "action": "Buy",
            "symbol": "NQM5",
            "orderQty": 1,
            "orderType": "Stop",
            "stopPrice": 20000.0,  # Entry at 20000
            "timeInForce": "GTC",
            "isAutomated": True
        }
        
        take_profit_order = {
            "accountSpec": client.account_spec,
            "accountId": client.account_id,
            "action": "Sell",
            "symbol": "NQM5",
            "orderQty": 1,
            "orderType": "Limit",
            "price": 20100.0,  # Take profit at 20100
            "timeInForce": "GTC",
            "isAutomated": True
        }
        
        try:
            logging.info("Placing OCO order (entry stop + take profit limit)")
            oco_result = await client.place_oco_order(entry_order, take_profit_order)
            logging.info(f"✅ OCO order placed successfully: {json.dumps(oco_result, indent=2)}")
        except Exception as e:
            logging.warning(f"⚠️ OCO order failed: {e}")
            
            # Fallback to OSO test
            logging.info("Testing OSO fallback")
            try:
                oso_payload = {
                    "accountSpec": client.account_spec,
                    "accountId": client.account_id,
                    "action": "Buy",
                    "symbol": "NQM5",
                    "orderQty": 1,
                    "orderType": "Stop",
                    "stopPrice": 20000.0,
                    "timeInForce": "GTC",
                    "isAutomated": True,
                    "bracket1": {
                        "action": "Sell",
                        "orderType": "Limit",
                        "price": 20100.0,  # Take profit
                        "timeInForce": "GTC"
                    },
                    "bracket2": {
                        "action": "Sell",
                        "orderType": "Stop",
                        "stopPrice": 19900.0,  # Stop loss
                        "timeInForce": "GTC"
                    }
                }
                
                oso_result = await client.place_oso_order(oso_payload)
                logging.info(f"✅ OSO fallback successful: {json.dumps(oso_result, indent=2)}")
            except Exception as oso_e:
                logging.error(f"❌ Both OCO and OSO failed: {oso_e}")
        
        # Step 5: Final check of pending orders
        logging.info("\n=== FINAL PENDING ORDERS CHECK ===")
        final_orders = await client.get_pending_orders()
        logging.info(f"✅ Final pending orders count: {len(final_orders)}")
        
        if final_orders:
            logging.info("Final pending orders:")
            for order in final_orders:
                logging.info(f"  - Order ID: {order.get('id')}, Symbol: {order.get('symbol')}, "
                           f"Action: {order.get('action')}, Type: {order.get('orderType')}, "
                           f"Status: {order.get('ordStatus')}")
        
        logging.info("\n=== ORDER MANAGEMENT TEST COMPLETED ===")
        
    except Exception as e:
        logging.error(f"❌ Test failed: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    print("🧪 Testing Tradovate Order Management Functionality")
    print("This test will:")
    print("1. Authenticate with Tradovate API")
    print("2. Get current pending orders")
    print("3. Test order cancellation")
    print("4. Test OCO/OSO order placement")
    print("5. Verify final state")
    print("\n" + "="*50)
    
    asyncio.run(test_order_management())
