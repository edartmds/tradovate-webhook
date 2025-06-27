#!/usr/bin/env python3

import requests
import json
import time

# Test the deployed webhook with a proper TradingView alert and check bracket status
webhook_url = "https://tradovate-webhook.onrender.com/"

# Test with proper TradingView alert format
test_alert = {
    "symbol": "NQ1!",
    "action": "buy",  # This will be flipped to sell
    "PRICE": 23000.0,
    "T1": 23050.0,   # This will become stop loss
    "STOP": 22950.0  # This will become take profit
}

print("=== Testing webhook with BUY alert (will be flipped to SELL) ===")
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
    
    try:
        response_data = response.json()
        print(f"Response JSON: {json.dumps(response_data, indent=2)}")
        
        # Check if bracket monitoring is active
        if response_data.get("bracket_monitoring") == "active":
            print("✅ Bracket monitoring is ACTIVE")
        elif response_data.get("bracket_monitoring") == "failed":
            print("❌ Bracket monitoring FAILED")
        else:
            print("⚠️ Bracket monitoring status unknown")
            
        # Verify flipping logic
        original_action = response_data.get("original_alert")
        flipped_action = response_data.get("flipped_action")
        print(f"\n🔄 FLIPPING VERIFICATION:")
        print(f"   Original: {original_action} → Flipped: {flipped_action}")
        
        # Verify bracket swapping
        brackets = response_data.get("pending_brackets", {})
        take_profit = brackets.get("take_profit")
        stop_loss = brackets.get("stop_loss")
        print(f"\n🔄 BRACKET SWAPPING VERIFICATION:")
        print(f"   Original T1 (23050) → New Stop Loss: {stop_loss}")
        print(f"   Original STOP (22950) → New Take Profit: {take_profit}")
        
        if stop_loss == 23050.0 and take_profit == 22950.0:
            print("✅ Bracket swapping is CORRECT")
        else:
            print("❌ Bracket swapping is INCORRECT")
            
    except:
        print(f"Response Text: {response.text}")
        
except Exception as e:
    print(f"Error: {e}")

print("\n" + "="*60)
print("=== Summary ===")
print("✅ If you see 'bracket_monitoring': 'active' above, the system is working correctly")
print("✅ The webhook should flip BUY→SELL and swap T1↔STOP values")
print("✅ Bracket orders will be placed automatically after entry fills")
print("❌ If bracket_monitoring is 'failed', there's an issue with order ID extraction")
