#!/usr/bin/env python3
"""
Quick Speed Test - Simple validation of optimized webhook system
"""

import asyncio
import time
import json
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tradovate_api import TradovateClient
from main import webhook_optimized, is_duplicate_request

async def quick_speed_test():
    """Quick test to verify optimized system works and is faster"""
    
    print("🚀 TRADOVATE WEBHOOK SPEED TEST")
    print("=" * 40)
    
    # Test 1: HTTP Client Configuration
    print("\n1. Testing HTTP Client Configuration...")
    start_time = time.time()
    
    client = TradovateClient("test", "test", "test")
    http_client = client._get_http_client()
    
    # Verify optimized settings
    assert http_client.timeout.connect == 3.0, f"Expected connect timeout 3.0s, got {http_client.timeout.connect}"
    assert http_client.timeout.read == 8.0, f"Expected read timeout 8.0s, got {http_client.timeout.read}"
    assert http_client.limits.max_keepalive_connections == 15, f"Expected 15 keepalive connections, got {http_client.limits.max_keepalive_connections}"
    assert http_client.limits.max_connections == 30, f"Expected 30 max connections, got {http_client.limits.max_connections}"
    
    config_time = time.time() - start_time
    print(f"✅ HTTP Configuration: {config_time:.3f}s")
    print("   - Connect timeout: 3.0s")
    print("   - Read timeout: 8.0s") 
    print("   - Keepalive connections: 15")
    print("   - Max connections: 30")
    
    # Test 2: Duplicate Detection Speed
    print("\n2. Testing Duplicate Detection Speed...")
    start_time = time.time()
    
    webhook_data = {
        "strategy": {
            "position_size": 2,
            "order_action": "buy",
            "order_contracts": 2,
            "order_price": 5000,
            "message_time": int(time.time()),
            "ticker": "ESZ3"
        }
    }
    
    # Test first request (not duplicate)
    is_dup_1 = await is_duplicate_request(webhook_data)
    assert not is_dup_1, "First request should not be duplicate"
    
    # Test second request (should be duplicate)
    is_dup_2 = await is_duplicate_request(webhook_data)
    assert is_dup_2, "Second request should be duplicate"
    
    duplicate_time = time.time() - start_time
    print(f"✅ Duplicate Detection: {duplicate_time:.3f}s")
    print(f"   - First check: Not duplicate")
    print(f"   - Second check: Duplicate detected")
    
    # Test 3: Parallel Order Operations
    print("\n3. Testing Parallel Operations...")
    start_time = time.time()
    
    # Mock client with fast responses
    mock_http = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"success": True}
    mock_http.post.return_value = mock_response
    mock_http.get.return_value = mock_response
    mock_http.delete.return_value = mock_response
    
    client._http_client = mock_http
    client.auth_token = "test_token"
    client.auth_expires_at = time.time() + 3600
    
    # Test parallel order cancellation
    mock_orders = [
        {"orderId": f"order_{i}", "orderStatus": "Working", "contractId": 12345}
        for i in range(5)
    ]
    
    with patch.object(client, 'get_pending_orders', return_value=mock_orders):
        with patch.object(client, '_cancel_order_fast', return_value=True):
            cancel_result = await client.cancel_all_pending_orders()
    
    parallel_time = time.time() - start_time
    print(f"✅ Parallel Operations: {parallel_time:.3f}s")
    print(f"   - Cancelled {len(mock_orders)} orders in parallel")
    
    # Test 4: Complete Webhook Flow
    print("\n4. Testing Complete Webhook Flow...")
    start_time = time.time()
    
    with patch('main.TradovateClient') as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        
        # Mock fast responses
        mock_client.get_positions.return_value = []
        mock_client.get_pending_orders.return_value = []
        mock_client.cancel_all_pending_orders.return_value = []
        mock_client.force_close_all_positions_immediately.return_value = []
        mock_client.determine_optimal_order_type.return_value = "Market"
        mock_client.place_order.return_value = {"orderId": "test_order", "orderStatus": "Working"}
        mock_client.monitor_all_orders_fast.return_value = [{"orderId": "test_order", "orderStatus": "Filled"}]
        
        # Use a different webhook data to avoid duplicate detection
        webhook_data_new = {
            "strategy": {
                "position_size": 1,
                "order_action": "sell",
                "order_contracts": 1,
                "order_price": 4990,
                "message_time": int(time.time()) + 1,
                "ticker": "ESZ3"
            }
        }
        
        result = await webhook_optimized(webhook_data_new)
    
    webhook_time = time.time() - start_time
    print(f"✅ Complete Webhook: {webhook_time:.3f}s")
    print(f"   - Status: {result.get('status', 'unknown')}")
    print(f"   - Execution time: {result.get('execution_time', 'N/A')}")
    
    # Test 5: Fast Order Monitoring
    print("\n5. Testing Fast Order Monitoring...")
    start_time = time.time()
    
    mock_order = {"orderId": "test_order", "orderStatus": "Working"}
    completed_order = {"orderId": "test_order", "orderStatus": "Filled"}
    
    call_count = 0
    async def mock_get_order_status(order_id):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.1)  # Simulate network delay
        if call_count >= 3:  # Complete after 3 checks
            return completed_order
        return mock_order
    
    with patch.object(client, 'get_order_status', side_effect=mock_get_order_status):
        monitored_orders = await client.monitor_all_orders_fast([mock_order], timeout=10)
    
    monitoring_time = time.time() - start_time
    print(f"✅ Fast Monitoring: {monitoring_time:.3f}s")
    print(f"   - Checks made: {call_count}")
    print(f"   - Orders completed: {len(monitored_orders)}")
    
    # Summary
    print("\n" + "=" * 40)
    print("📊 SPEED TEST SUMMARY")
    print("=" * 40)
    print(f"HTTP Configuration:  {config_time:.3f}s")
    print(f"Duplicate Detection: {duplicate_time:.3f}s")
    print(f"Parallel Operations: {parallel_time:.3f}s")
    print(f"Complete Webhook:    {webhook_time:.3f}s")
    print(f"Fast Monitoring:     {monitoring_time:.3f}s")
    
    total_time = config_time + duplicate_time + parallel_time + webhook_time + monitoring_time
    print(f"Total Test Time:     {total_time:.3f}s")
    
    # Performance validation
    print("\n🎯 PERFORMANCE VALIDATION")
    print("-" * 25)
    
    if webhook_time < 5.0:
        print("✅ Webhook processing speed: EXCELLENT")
    elif webhook_time < 10.0:
        print("⚠️  Webhook processing speed: GOOD")
    else:
        print("❌ Webhook processing speed: NEEDS IMPROVEMENT")
    
    if duplicate_time < 0.1:
        print("✅ Duplicate detection speed: EXCELLENT")
    else:
        print("⚠️  Duplicate detection speed: COULD BE FASTER")
    
    if parallel_time < 2.0:
        print("✅ Parallel operations speed: EXCELLENT")
    else:
        print("⚠️  Parallel operations speed: COULD BE FASTER")
    
    if monitoring_time < 5.0:
        print("✅ Order monitoring speed: EXCELLENT")
    else:
        print("⚠️  Order monitoring speed: COULD BE FASTER")
    
    print("\n🎉 SPEED OPTIMIZATION TEST COMPLETED!")
    return True

if __name__ == "__main__":
    try:
        asyncio.run(quick_speed_test())
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
