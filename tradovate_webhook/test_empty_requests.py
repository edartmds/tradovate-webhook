#!/usr/bin/env python3

import requests
import json

# Test with empty request (like health checks)
webhook_url = "https://tradovate-webhook.onrender.com/"

print("=== Testing webhook with empty request ===")

try:
    response = requests.post(
        webhook_url,
        data="",
        headers={"Content-Type": "text/plain"},
        timeout=30
    )
    
    print(f"Response Status: {response.status_code}")
    
    try:
        response_data = response.json()
        print(f"Response JSON: {json.dumps(response_data, indent=2)}")
    except:
        print(f"Response Text: {response.text}")
        
except Exception as e:
    print(f"Error: {e}")

print("\n=== Testing webhook with malformed request ===")

try:
    response = requests.post(
        webhook_url,
        data="invalid_data_here",
        headers={"Content-Type": "text/plain"},
        timeout=30
    )
    
    print(f"Response Status: {response.status_code}")
    
    try:
        response_data = response.json()
        print(f"Response JSON: {json.dumps(response_data, indent=2)}")
    except:
        print(f"Response Text: {response.text}")
        
except Exception as e:
    print(f"Error: {e}")
