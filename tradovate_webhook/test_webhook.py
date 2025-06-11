#!/usr/bin/env python3
"""
Test script to send a sample TradingView alert to the webhook endpoint
"""
import requests
import json

# Sample TradingView alert message
alert_message = """={"settlement-as-close":true,"symbol":"CME_MINI:NQ1!"}
BUY
PRICE=21402.25
T1=21407.0445
T2=21411.71
T3=21416.44
STOP=21391.25"""

# Webhook URL (change to your deployed URL when testing remotely)
webhook_url = "http://localhost:10000/webhook"

def test_webhook():
    print("Testing webhook with sample TradingView alert...")
    print(f"Alert message:\n{alert_message}")
    print(f"Webhook URL: {webhook_url}")
    
    try:
        response = requests.post(
            webhook_url,
            data=alert_message,
            headers={"Content-Type": "text/plain"},
            timeout=30
        )
        
        print(f"\nResponse Status: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        print(f"Response Body: {response.text}")
        
        if response.status_code == 200:
            print("✅ Webhook test successful!")
        else:
            print("❌ Webhook test failed!")
            
    except Exception as e:
        print(f"❌ Error testing webhook: {e}")

def test_multiple_alerts():
    """Test multiple alerts to verify order cancellation logic"""
    print("\n" + "="*50)
    print("Testing multiple alerts to verify order cancellation...")
    
    # First alert - BUY
    buy_alert = """={"settlement-as-close":true,"symbol":"CME_MINI:NQ1!"}
BUY
PRICE=21402.25
T1=21407.0445
T2=21411.71
T3=21416.44
STOP=21391.25"""

    # Second alert - SELL (should cancel previous orders)
    sell_alert = """={"settlement-as-close":true,"symbol":"CME_MINI:NQ1!"}
SELL
PRICE=21400.00
T1=21395.50
T2=21391.00
T3=21386.50
STOP=21410.00"""

    alerts = [
        ("First BUY Alert", buy_alert),
        ("Second SELL Alert (should cancel previous)", sell_alert)
    ]
    
    for alert_name, alert_data in alerts:
        print(f"\n--- {alert_name} ---")
        try:
            headers = {"Content-Type": "text/plain"}
            response = requests.post(webhook_url, data=alert_data, headers=headers, timeout=30)
            
            print(f"Status: {response.status_code}")
            if response.status_code == 200:
                print(f"✅ {alert_name} successful!")
                try:
                    result = json.loads(response.text) if response.text else {}
                    print(f"Response: {result}")
                except:
                    print(f"Response: {response.text}")
            else:
                print(f"❌ {alert_name} failed!")
                print(f"Error: {response.text}")
                
            # Wait a bit between alerts
            import time
            time.sleep(3)
            
        except Exception as e:
            print(f"❌ Error with {alert_name}: {e}")

if __name__ == "__main__":
    print("🧪 Testing Tradovate Webhook with Order Cancellation")
    print("This will test:")
    print("1. Single alert processing")
    print("2. Multiple alerts with order cancellation")
    print("3. OCO/OSO bracket order placement")
    
    # Test single alert
    test_webhook()
    
    # Test multiple alerts to verify cancellation
    test_multiple_alerts()
    
    print("\n" + "="*50)
    print("Test completed. Check the webhook logs for detailed results.")
