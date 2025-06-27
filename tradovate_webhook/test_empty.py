import httpx
import json

# Test empty request to see what the improved error handling does
webhook_url = "https://tradovate-webhook.onrender.com/webhook"

async def test_empty_request():
    async with httpx.AsyncClient() as client:
        try:
            print("🧪 Testing empty request...")
            response = await client.post(
                webhook_url,
                data="",  # Empty body
                headers={"Content-Type": "text/plain"},
                timeout=30.0
            )
            
            print(f"✅ Response status: {response.status_code}")
            print(f"📝 Response: {response.text}")
            
        except Exception as e:
            print(f"❌ Request failed: {e}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_empty_request())
