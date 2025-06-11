#!/usr/bin/env python3
"""
Test script for position management functionality in the Tradovate webhook.
This script tests the position closure functionality.
"""

import asyncio
import json
import logging
from tradovate_api import TradovateClient

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def test_position_management():
    """Test position management functionality"""
    client = TradovateClient()
    
    try:
        # Step 1: Authenticate
        logging.info("=== TESTING AUTHENTICATION ===")
        await client.authenticate()
        logging.info(f"✅ Authentication successful")
        logging.info(f"Account ID: {client.account_id}")
        logging.info(f"Account Spec: {client.account_spec}")
        
        # Step 2: Get current positions
        logging.info("\n=== TESTING GET POSITIONS ===")
        positions = await client.get_positions()
        logging.info(f"✅ Found {len(positions)} open positions")
        
        if positions:
            logging.info("Current open positions:")
            for position in positions:
                symbol = position.get("symbol")
                net_pos = position.get("netPos", 0)
                logging.info(f"  - Symbol: {symbol}, NetPos: {net_pos}")
        else:
            logging.info("ℹ️ No open positions found")
        
        # Step 3: Test individual position closure (if any positions exist)
        if positions:
            logging.info("\n=== TESTING INDIVIDUAL POSITION CLOSURE ===")
            first_position = positions[0]
            symbol = first_position.get("symbol")
            
            logging.info(f"Testing closure of position for {symbol}")
            try:
                result = await client.close_position(symbol)
                logging.info(f"✅ Position closure result: {json.dumps(result, indent=2)}")
            except Exception as e:
                logging.warning(f"⚠️ Failed to close position for {symbol}: {e}")
        
        # Step 4: Get updated positions after individual closure
        logging.info("\n=== CHECKING POSITIONS AFTER INDIVIDUAL CLOSURE ===")
        updated_positions = await client.get_positions()
        logging.info(f"✅ Found {len(updated_positions)} open positions after individual closure")
        
        # Step 5: Test close all remaining positions
        if updated_positions:
            logging.info("\n=== TESTING CLOSE ALL POSITIONS ===")
            try:
                closed_positions = await client.close_all_positions()
                logging.info(f"✅ Successfully closed {len(closed_positions)} positions")
                
                for result in closed_positions:
                    logging.info(f"Closed position result: {json.dumps(result, indent=2)}")
            except Exception as e:
                logging.warning(f"⚠️ Failed to close all positions: {e}")
        else:
            logging.info("ℹ️ No remaining positions to test closure")
        
        # Step 6: Final check of positions
        logging.info("\n=== FINAL POSITIONS CHECK ===")
        final_positions = await client.get_positions()
        logging.info(f"✅ Final open positions count: {len(final_positions)}")
        
        if final_positions:
            logging.info("Final open positions:")
            for position in final_positions:
                symbol = position.get("symbol")
                net_pos = position.get("netPos", 0)
                logging.info(f"  - Symbol: {symbol}, NetPos: {net_pos}")
        else:
            logging.info("✅ All positions successfully closed")
        
        # Step 7: Test closing non-existent position
        logging.info("\n=== TESTING CLOSURE OF NON-EXISTENT POSITION ===")
        try:
            result = await client.close_position("NONEXISTENT")
            logging.info(f"✅ Non-existent position result: {json.dumps(result, indent=2)}")
        except Exception as e:
            logging.warning(f"⚠️ Error handling non-existent position: {e}")
        
        logging.info("\n=== POSITION MANAGEMENT TEST COMPLETED ===")
        
    except Exception as e:
        logging.error(f"❌ Test failed: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    print("🧪 Testing Tradovate Position Management Functionality")
    print("This test will:")
    print("1. Authenticate with Tradovate API")
    print("2. Get current open positions")
    print("3. Test individual position closure")
    print("4. Test close all positions")
    print("5. Verify final state")
    print("6. Test edge cases")
    print("\n" + "="*50)
    
    asyncio.run(test_position_management())
