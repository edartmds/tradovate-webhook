#!/usr/bin/env python3

import requests
import json

# Test the deployed webhook with a proper TradingView alert
webhook_url = "https://tradovate-webhook.onrender.com/"

# Test with proper TradingView alert format
test_alert = {
    "symbol": "NQ1!",
    "action": "sell",
    "PRICE": 22000.0,
    "T1": 22100.0,
    "STOP": 21900.0
}

print("=== Testing webhook with proper alert data ===")
print(f"URL: {webhook_url}")
print(f"Alert data: {json.dumps(test_alert, indent=2)}")

try:
    response = requests.post(
        webhook_url,
        json=test_alert,
        headers={"Content-Type": "application/json"},
        timeout=30
    )
    
    print(f"\nResponse Status: {response.status_code}")
    print(f"Response Headers: {dict(response.headers)}")
    
    try:
        response_data = response.json()
        print(f"Response JSON: {json.dumps(response_data, indent=2)}")
    except:
        print(f"Response Text: {response.text}")
        
except Exception as e:
    print(f"Error: {e}")

print("\n=== Testing with key=value format ===")

# Test with key=value format (like TradingView sends)
test_data = "symbol=NQ1!\naction=sell\nPRICE=22000\nT1=22100\nSTOP=21900"

try:
    response = requests.post(
        webhook_url,
        data=test_data,
        headers={"Content-Type": "text/plain"},
        timeout=30
    )
    
    print(f"\nResponse Status: {response.status_code}")
    print(f"Response Headers: {dict(response.headers)}")
    
    try:
        response_data = response.json()
        print(f"Response JSON: {json.dumps(response_data, indent=2)}")
    except:
        print(f"Response Text: {response.text}")
        
except Exception as e:
    print(f"Error: {e}")
