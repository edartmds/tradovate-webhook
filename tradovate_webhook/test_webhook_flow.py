import asyncio
import httpx
import json

async def test_webhook_flow():
    """
    Test the complete webhook flow with position closure.
    """
    print("🔥🔥🔥 TESTING COMPLETE WEBHOOK FLOW 🔥🔥🔥")
    
    # Sample TradingView alert
    test_alert = """={
    "symbol": "NQ1!",
    "timestamp": "2024-01-01T12:00:00Z"
}
BUY
PRICE=18500.00
T1=18550.00
T2=18600.00
T3=18650.00
STOP=18450.00"""
    
    webhook_url = "http://localhost:10000/webhook"
    
    try:
        async with httpx.AsyncClient() as client:
            print("📤 Sending test alert to webhook...")
            print(f"Alert data:\n{test_alert}")
            
            response = await client.post(
                webhook_url,
                content=test_alert,
                headers={"Content-Type": "text/plain"},
                timeout=60.0
            )
            
            if response.status_code == 200:
                result = response.json()
                print("✅ Webhook processed successfully!")
                print(f"Response: {json.dumps(result, indent=2)}")
            else:
                print(f"❌ Webhook failed with status {response.status_code}")
                print(f"Error: {response.text}")
                
    except httpx.ConnectError:
        print("❌ Could not connect to webhook server")
        print("   Make sure the webhook server is running: python main.py")
    except Exception as e:
        print(f"❌ Error testing webhook: {e}")

if __name__ == "__main__":
    asyncio.run(test_webhook_flow())
