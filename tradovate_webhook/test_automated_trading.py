#!/usr/bin/env python3
"""
🔥 AUTOMATED TRADING DUPLICATE DETECTION TEST
Tests the new relaxed duplicate detection system
"""

import asyncio
import json
from datetime import datetime, timedelta
import sys
import os

# Add the current directory to Python path
sys.path.append(os.path.dirname(__file__))

from main import is_duplicate_alert, hash_alert, cleanup_old_tracking_data

def test_automated_trading_scenarios():
    """Test various automated trading scenarios that should now be ALLOWED"""
    
    print("🔥 TESTING AUTOMATED TRADING DUPLICATE DETECTION")
    print("=" * 60)
    
    # Test data
    base_alert = {
        "symbol": "NQM5",
        "action": "Buy",
        "PRICE": 18500.0,
        "T1": 18550.0,
        "STOP": 18450.0
    }
    
    # Test 1: Direction Changes (should be ALLOWED)
    print("\n📊 TEST 1: DIRECTION CHANGES")
    print("-" * 30)
    
    buy_alert = base_alert.copy()
    sell_alert = base_alert.copy()
    sell_alert["action"] = "Sell"
    
    result1 = is_duplicate_alert("NQM5", "Buy", buy_alert)
    print(f"1. BUY alert: {'❌ BLOCKED' if result1 else '✅ ALLOWED'}")
    
    result2 = is_duplicate_alert("NQM5", "Sell", sell_alert)
    print(f"2. SELL alert (after BUY): {'❌ BLOCKED' if result2 else '✅ ALLOWED'}")
    
    result3 = is_duplicate_alert("NQM5", "Buy", buy_alert)
    print(f"3. BUY alert (after SELL): {'❌ BLOCKED' if result3 else '✅ ALLOWED'}")
    
    # Test 2: Same Direction Updates (should be ALLOWED)
    print("\n📊 TEST 2: SAME DIRECTION UPDATES")
    print("-" * 30)
    
    cleanup_old_tracking_data()  # Clean slate
    
    buy1 = base_alert.copy()
    buy2 = base_alert.copy()
    buy2["PRICE"] = 18520.0  # Different price
    buy3 = base_alert.copy()
    buy3["PRICE"] = 18540.0  # Different price again
    
    result1 = is_duplicate_alert("NQM5", "Buy", buy1)
    print(f"1. BUY @ 18500: {'❌ BLOCKED' if result1 else '✅ ALLOWED'}")
    
    result2 = is_duplicate_alert("NQM5", "Buy", buy2)
    print(f"2. BUY @ 18520: {'❌ BLOCKED' if result2 else '✅ ALLOWED'}")
    
    result3 = is_duplicate_alert("NQM5", "Buy", buy3)
    print(f"3. BUY @ 18540: {'❌ BLOCKED' if result3 else '✅ ALLOWED'}")
    
    # Test 3: Rapid-Fire Identical (should be BLOCKED only within 30 seconds)
    print("\n📊 TEST 3: RAPID-FIRE IDENTICAL ALERTS")
    print("-" * 30)
    
    cleanup_old_tracking_data()  # Clean slate
    
    identical1 = base_alert.copy()
    identical2 = base_alert.copy()  # Exactly the same
    
    result1 = is_duplicate_alert("NQM5", "Buy", identical1)
    print(f"1. First identical alert: {'❌ BLOCKED' if result1 else '✅ ALLOWED'}")
    
    result2 = is_duplicate_alert("NQM5", "Buy", identical2)
    print(f"2. Second identical alert (immediate): {'❌ BLOCKED' if result2 else '✅ ALLOWED'}")
    
    # Test 4: Frequency Test (should be ALLOWED)
    print("\n📊 TEST 4: HIGH-FREQUENCY TRADING")
    print("-" * 30)
    
    cleanup_old_tracking_data()  # Clean slate
    
    alerts = []
    for i in range(5):
        alert = base_alert.copy()
        alert["PRICE"] = 18500 + (i * 10)  # Slightly different prices
        alerts.append(alert)
    
    for i, alert in enumerate(alerts):
        result = is_duplicate_alert("NQM5", "Buy", alert)
        print(f"{i+1}. BUY @ {alert['PRICE']}: {'❌ BLOCKED' if result else '✅ ALLOWED'}")
    
    # Summary
    print("\n🎯 EXPECTED RESULTS:")
    print("✅ Direction changes: ALLOWED (BUY→SELL→BUY)")
    print("✅ Same direction updates: ALLOWED (different prices)")
    print("❌ Rapid-fire identical: BLOCKED (within 30 seconds)")
    print("✅ High-frequency trading: ALLOWED (different parameters)")
    print("\n🚀 System optimized for AUTOMATED TRADING with position flattening!")

if __name__ == "__main__":
    test_automated_trading_scenarios()
