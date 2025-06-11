import asyncio
import json
import logging
from tradovate_api import TradovateClient

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def test_stop_entry_oso():
    """
    Test the OSO order structure with Stop order entry trigger.
    """
    print("🔥🔥🔥 TESTING STOP ENTRY OSO ORDER 🔥🔥🔥")
    
    client = TradovateClient()
    
    try:
        # Authenticate
        print("🔐 Authenticating...")
        await client.authenticate()
        print(f"✅ Authenticated - Account: {client.account_spec}")
        
        # Test data for Stop entry
        symbol = "NQM5"
        action = "Buy"  # Buy when price breaks above entry level
        entry_price = 18500.00  # Stop entry triggers when price hits this level
        take_profit = 18550.00  # Take profit 50 points above
        stop_loss = 18450.00    # Stop loss 50 points below
        opposite_action = "Sell"
        
        print(f"📊 Testing Stop Entry OSO:")
        print(f"   Symbol: {symbol}")
        print(f"   Action: {action} (entry when price reaches {entry_price})")
        print(f"   Entry Type: Stop Order (triggers at {entry_price})")
        print(f"   Take Profit: {take_profit}")
        print(f"   Stop Loss: {stop_loss}")
        
        # Create the OSO payload with Stop entry
        oso_payload = {
            "accountSpec": client.account_spec,
            "accountId": client.account_id,
            "action": action,
            "symbol": symbol,
            "orderQty": 1,
            "orderType": "Stop",     # Stop order for breakout entry
            "stopPrice": entry_price, # Entry triggers when price hits this level
            "timeInForce": "GTC",
            "isAutomated": True,
            "bracket1": {
                "accountSpec": client.account_spec,
                "accountId": client.account_id,
                "action": opposite_action,
                "symbol": symbol,
                "orderQty": 1,
                "orderType": "Limit",
                "price": take_profit,
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
                "stopPrice": stop_loss,
                "timeInForce": "GTC",
                "isAutomated": True
            }
        }
        
        print("\n📋 STOP ENTRY OSO PAYLOAD:")
        print(json.dumps(oso_payload, indent=2))
        
        # Test the OSO order placement
        print("\n🚀 TESTING STOP ENTRY OSO ORDER PLACEMENT...")
        try:
            result = await client.place_oso_order(oso_payload)
            print("✅ STOP ENTRY OSO ORDER PLACED SUCCESSFULLY!")
            print(f"Result: {json.dumps(result, indent=2)}")
            
            print("\n🎯 EXPECTED BEHAVIOR:")
            print(f"   1. Entry order waits for price to reach {entry_price}")
            print(f"   2. When price hits {entry_price}, {action} order executes")
            print(f"   3. Take profit bracket at {take_profit} becomes active")
            print(f"   4. Stop loss bracket at {stop_loss} becomes active")
            print(f"   5. Trade exits when either TP or SL is hit")
            
        except Exception as e:
            print(f"❌ STOP ENTRY OSO ORDER FAILED: {e}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
        
    except Exception as e:
        print(f"❌ ERROR during testing: {e}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    asyncio.run(test_stop_entry_oso())
