import asyncio
import json
import logging
from tradovate_api import TradovateClient

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def test_oso_structure():
    """
    Test the OSO order structure to ensure it matches Tradovate API requirements.
    """
    print("🔥🔥🔥 TESTING OSO ORDER STRUCTURE 🔥🔥🔥")
    
    client = TradovateClient()
    
    try:
        # Authenticate
        print("🔐 Authenticating...")
        await client.authenticate()
        print(f"✅ Authenticated - Account: {client.account_spec}")
        
        # Test data matching your alerts
        symbol = "NQM5"
        action = "Buy"
        price = 18500.00
        t1 = 18550.00
        stop = 18450.00
        opposite_action = "Sell"
          # Create the exact OSO payload structure
        oso_payload = {
            "accountSpec": client.account_spec,
            "accountId": client.account_id,
            "action": action,
            "symbol": symbol,
            "orderQty": 1,
            "orderType": "Stop",    # Stop order for entry trigger
            "stopPrice": price,     # Entry triggers at this price
            "timeInForce": "GTC",
            "isAutomated": True,
            "bracket1": {
                "accountSpec": client.account_spec,
                "accountId": client.account_id,
                "action": opposite_action,
                "symbol": symbol,
                "orderQty": 1,
                "orderType": "Limit",
                "price": t1,
                "timeInForce": "GTC",
                "isAutomated": True
            },
            "bracket2": {
                "accountSpec": client.account_spec,
                "accountId": client.account_id,
                "action": opposite_action,
                "symbol": symbol,
                "orderQty": 1,
                "orderType": "Stop",
                "stopPrice": stop,
                "timeInForce": "GTC",
                "isAutomated": True
            }
        }
        
        print("📊 TESTING OSO PAYLOAD STRUCTURE:")
        print(json.dumps(oso_payload, indent=2))
        
        # Test the OSO order placement
        print("\n🚀 TESTING OSO ORDER PLACEMENT...")
        try:
            result = await client.place_oso_order(oso_payload)
            print("✅ OSO ORDER PLACED SUCCESSFULLY!")
            print(f"Result: {json.dumps(result, indent=2)}")
        except Exception as e:
            print(f"❌ OSO ORDER FAILED: {e}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
        
    except Exception as e:
        print(f"❌ ERROR during testing: {e}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    asyncio.run(test_oso_structure())
