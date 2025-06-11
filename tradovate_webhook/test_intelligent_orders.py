#!/usr/bin/env python3
"""
Test script for intelligent order type selection
Tests the logic that determines whether to use Stop vs Limit orders
"""

import asyncio
import os
import sys
import logging
from tradovate_api import TradovateClient

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

async def test_intelligent_order_selection():
    """Test the intelligent order type selection with various scenarios"""
    
    try:
        # Initialize client
        client = TradovateClient()
        await client.initialize()
        
        # Get current active NQ contract
        symbol = await client.get_active_contract_symbol("NQ")
        print(f"🎯 Testing with active contract: {symbol}")
        
        # Test scenarios
        test_cases = [
            {
                "name": "BUY Breakout (price above market)",
                "action": "Buy", 
                "target_price": 20000,  # High price for breakout
                "expected": "Stop"
            },
            {
                "name": "BUY Dip (price below market)", 
                "action": "Buy",
                "target_price": 18000,  # Low price for dip buying
                "expected": "Limit"
            },
            {
                "name": "SELL Breakdown (price below market)",
                "action": "Sell",
                "target_price": 18000,  # Low price for breakdown
                "expected": "Stop"  
            },
            {
                "name": "SELL Rally (price above market)",
                "action": "Sell", 
                "target_price": 20000,  # High price for selling rally
                "expected": "Limit"
            }
        ]
        
        # Get current market data first
        print(f"\n📊 Getting current market data for {symbol}...")
        market_data = await client.get_market_data(symbol)
        if market_data:
            current_price = market_data.get('last', market_data.get('bid', market_data.get('ask', 19000)))
            print(f"💰 Current market price: {current_price}")
        else:
            current_price = 19000  # Fallback price
            print(f"⚠️ Could not get market data, using fallback price: {current_price}")
        
        print(f"\n🧪 Running test cases...\n")
        
        # Run tests
        for i, test in enumerate(test_cases, 1):
            print(f"--- Test {i}: {test['name']} ---")
            print(f"Action: {test['action']}")
            print(f"Target Price: {test['target_price']}")
            print(f"Current Price: {current_price}")
            
            try:
                result = await client.determine_optimal_order_type(
                    symbol, 
                    test['action'], 
                    test['target_price']
                )
                
                order_type = result['orderType']
                print(f"Result: {order_type}")
                print(f"Expected: {test['expected']}")
                
                if order_type == test['expected']:
                    print("✅ PASS")
                else:
                    print("❌ FAIL")
                    
                # Show the logic
                if order_type == "Stop":
                    print(f"🎯 Stop Price: {result.get('stopPrice')}")
                else:
                    print(f"💰 Limit Price: {result.get('price')}")
                    
            except Exception as e:
                print(f"❌ ERROR: {e}")
                
            print()
        
        print("🎯 Test Summary:")
        print("- Stop orders should be used for breakout/breakdown strategies")
        print("- Limit orders should be used for dip buying/rally selling")
        print("- This prevents 'price already at or past this level' errors")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_intelligent_order_selection())
