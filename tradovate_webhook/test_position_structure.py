#!/usr/bin/env python3
"""
🔥 POSITION STRUCTURE DEBUGGING TEST
This test will help us understand the actual Tradovate position object structure
"""

import asyncio
import logging
from tradovate_api import TradovateClient

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def test_position_structure():
    """Test to examine the actual position object structure returned by Tradovate API"""
    
    print("🔥 TESTING POSITION OBJECT STRUCTURE")
    print("=" * 50)
    
    try:
        # Initialize client
        client = TradovateClient()
        
        # Authenticate
        print("🔑 Authenticating with Tradovate...")
        await client.authenticate()
        print(f"✅ Authentication successful")
        print(f"   Account ID: {client.account_id}")
        print(f"   Account Spec: {client.account_spec}")
        
        # Get positions with enhanced debugging
        print("\n🔍 Retrieving positions with enhanced debugging...")
        positions = await client.get_positions()
        
        print(f"\n📊 POSITION ANALYSIS RESULTS:")
        print(f"   Total positions found: {len(positions)}")
        
        if positions:
            print(f"\n🔍 DETAILED POSITION ANALYSIS:")
            for i, pos in enumerate(positions, 1):
                print(f"\n   POSITION {i}:")
                print(f"   ├── netPos: {pos.get('netPos', 'NOT_FOUND')}")
                print(f"   ├── symbol: {pos.get('symbol', 'NOT_FOUND')}")
                print(f"   ├── contractName: {pos.get('contractName', 'NOT_FOUND')}")
                print(f"   ├── instrument: {pos.get('instrument', 'NOT_FOUND')}")
                print(f"   ├── contractId: {pos.get('contractId', 'NOT_FOUND')}")
                print(f"   ├── contract: {pos.get('contract', 'NOT_FOUND')}")
                print(f"   ├── masterInstrument: {pos.get('masterInstrument', 'NOT_FOUND')}")
                print(f"   ├── id: {pos.get('id', 'NOT_FOUND')}")
                print(f"   ├── positionId: {pos.get('positionId', 'NOT_FOUND')}")
                print(f"   └── All keys: {list(pos.keys())}")
                
                # Check nested objects
                if pos.get('masterInstrument'):
                    print(f"      masterInstrument details: {pos['masterInstrument']}")
                if pos.get('contract'):
                    print(f"      contract details: {pos['contract']}")
        else:
            print("   ⚠️ No open positions found")
            print("   This is expected if no trades are currently active")
            
        # Test the enhanced position closure
        print(f"\n🔥 TESTING ENHANCED POSITION CLOSURE...")
        success = await client.force_close_all_positions_immediately()
        print(f"   Position closure result: {'✅ SUCCESS' if success else '❌ FAILED'}")
        
    except Exception as e:
        print(f"❌ TEST FAILED: {e}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    asyncio.run(test_position_structure())
