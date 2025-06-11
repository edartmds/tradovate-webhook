import asyncio
import logging
from tradovate_api import TradovateClient

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def test_position_closure():
    """
    Test the position closure functionality to ensure it works properly.
    """
    print("🔥🔥🔥 TESTING POSITION CLOSURE FUNCTIONALITY 🔥🔥🔥")
    
    client = TradovateClient()
    
    try:
        # Authenticate
        print("🔐 Authenticating...")
        await client.authenticate()
        print(f"✅ Authenticated - Account: {client.account_spec}")
        
        # Check current positions
        print("\n📊 CHECKING CURRENT POSITIONS...")
        positions = await client.get_all_positions()
        
        if not positions:
            print("✅ No open positions found")
            return
        
        print(f"🔍 Found {len(positions)} open positions:")
        for pos in positions:
            symbol = pos.get("symbol", "Unknown")
            net_pos = pos.get("netPos", 0)
            print(f"   📊 {symbol}: Net Position = {net_pos}")
        
        # Test the force close functionality
        print("\n🔥 TESTING FORCE CLOSE FUNCTIONALITY...")
        success = await client.force_close_all_positions_immediately()
        
        if success:
            print("✅ Position closure test PASSED - All positions closed successfully!")
        else:
            print("❌ Position closure test FAILED - Some positions remain open")
        
        # Final verification
        print("\n🔍 FINAL POSITION CHECK...")
        final_positions = await client.get_all_positions()
        
        if not final_positions:
            print("✅ PERFECT! No positions remaining")
        else:
            print(f"⚠️ WARNING: {len(final_positions)} positions still open:")
            for pos in final_positions:
                symbol = pos.get("symbol", "Unknown")
                net_pos = pos.get("netPos", 0)
                print(f"   ⚠️ {symbol}: Net Position = {net_pos}")
        
    except Exception as e:
        print(f"❌ ERROR during testing: {e}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    asyncio.run(test_position_closure())
