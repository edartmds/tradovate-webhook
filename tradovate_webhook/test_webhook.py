import httpx
import json

# Test the webhook with a sample TradingView alert
webhook_url = "https://tradovate-webhook.onrender.com/webhook"

# Sample TradingView alert data (flipped strategy test)
test_alert = {
    "symbol": "NQ1!",
    "action": "buy",  # This will be flipped to SELL
    "PRICE": 20000.00,
    "T1": 20050.00,   # This will become STOP (stop loss) 
    "STOP": 19950.00  # This will become T1 (take profit)
}

print(f"🧪 Testing webhook at: {webhook_url}")
print(f"📊 Original alert: {json.dumps(test_alert, indent=2)}")
print(f"🔄 Expected flip: BUY → SELL, T1→STOP, STOP→T1")

async def test_webhook():
    async with httpx.AsyncClient() as client:
        try:
            # Test 1: Without authentication
            print("\n🧪 Test 1: Without authentication")
            response = await client.post(
                webhook_url,
                json=test_alert,
                headers={"Content-Type": "application/json"},
                timeout=30.0
            )
            
            print(f"✅ Response status: {response.status_code}")
            print(f"📝 Response body: {response.text}")
            
            if response.status_code != 200:
                # Test 2: With authentication token
                print("\n🧪 Test 2: With authentication token")
                response = await client.post(
                    webhook_url,
                    json=test_alert,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer new-super-secret-token"
                    },
                    timeout=30.0
                )
                
                print(f"✅ Response status: {response.status_code}")
                print(f"📝 Response body: {response.text}")
            
            if response.status_code == 200:
                try:
                    result = response.json()
                    print(f"🎉 Success! Order placed: {json.dumps(result, indent=2)}")
                except:
                    print(f"🎉 Success! Response: {response.text}")
            else:
                print(f"❌ Error: {response.status_code} - {response.text}")
                
        except Exception as e:
            print(f"❌ Request failed: {e}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_webhook())
