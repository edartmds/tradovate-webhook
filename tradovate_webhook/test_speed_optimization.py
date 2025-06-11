#!/usr/bin/env python3
"""
Speed Optimization Test Suite
Tests the optimized webhook system for performance improvements and functionality preservation.
"""

import asyncio
import time
import json
import httpx
import pytest
from typing import Dict, Any, List
from unittest.mock import AsyncMock, patch, MagicMock
import sys
import os

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tradovate_api import TradovateClient
from main import webhook_optimized

class SpeedTestMetrics:
    """Track performance metrics during testing"""
    
    def __init__(self):
        self.execution_times = []
        self.api_call_times = []
        self.order_processing_times = []
        self.position_closure_times = []
    
    def add_execution_time(self, operation: str, duration: float):
        self.execution_times.append({
            'operation': operation,
            'duration': duration,
            'timestamp': time.time()
        })
    
    def get_average_time(self, operation: str) -> float:
        times = [t['duration'] for t in self.execution_times if t['operation'] == operation]
        return sum(times) / len(times) if times else 0
    
    def print_summary(self):
        print("\n=== SPEED TEST SUMMARY ===")
        operations = set(t['operation'] for t in self.execution_times)
        for op in operations:
            avg_time = self.get_average_time(op)
            times = [t['duration'] for t in self.execution_times if t['operation'] == op]
            print(f"{op}: avg={avg_time:.3f}s, min={min(times):.3f}s, max={max(times):.3f}s, count={len(times)}")

# Global metrics tracker
metrics = SpeedTestMetrics()

async def time_operation(operation_name: str, coro):
    """Time an async operation and record metrics"""
    start_time = time.time()
    try:
        result = await coro
        duration = time.time() - start_time
        metrics.add_execution_time(operation_name, duration)
        print(f"✓ {operation_name}: {duration:.3f}s")
        return result
    except Exception as e:
        duration = time.time() - start_time
        print(f"✗ {operation_name}: {duration:.3f}s (FAILED: {e})")
        raise

class TestSpeedOptimization:
    """Test suite for speed optimizations"""
    
    @pytest.fixture
    async def mock_client(self):
        """Create a mock TradovateClient for testing"""
        client = TradovateClient("test_user", "test_pass", "test_secret")
        
        # Mock HTTP client with fast responses
        mock_http = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "test_token", "expires_in": 3600}
        mock_http.post.return_value = mock_response
        mock_http.get.return_value = mock_response
        mock_http.delete.return_value = mock_response
        
        client._http_client = mock_http
        client.auth_token = "test_token"
        client.auth_expires_at = time.time() + 3600
        
        return client
    
    @pytest.fixture
    def sample_webhook_data(self):
        """Sample webhook data for testing"""
        return {
            "strategy": {
                "position_size": 2,
                "order_action": "buy",
                "order_contracts": 2,
                "order_price": 5000,
                "message_time": int(time.time()),
                "ticker": "ESZ3"
            }
        }
    
    async def test_connection_pooling_speed(self, mock_client):
        """Test that connection pooling improves HTTP request speed"""
        print("\n=== Testing Connection Pooling Speed ===")
        
        # Test multiple consecutive requests
        tasks = []
        for i in range(10):
            task = time_operation(f"http_request_{i}", mock_client._make_authenticated_request("GET", "/test"))
            tasks.append(task)
        
        # Execute all requests
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Verify we have fast response times
        avg_time = metrics.get_average_time("http_request_0")  # Check first request as example
        assert avg_time < 0.1, f"HTTP requests too slow: {avg_time:.3f}s"
    
    async def test_parallel_order_cancellation(self, mock_client):
        """Test parallel order cancellation speed"""
        print("\n=== Testing Parallel Order Cancellation ===")
        
        # Mock pending orders
        mock_orders = [
            {"orderId": f"order_{i}", "orderStatus": "Working", "contractId": 12345}
            for i in range(5)
        ]
        
        with patch.object(mock_client, 'get_pending_orders', return_value=mock_orders):
            with patch.object(mock_client, '_cancel_order_fast', return_value=True):
                result = await time_operation(
                    "parallel_cancel_orders", 
                    mock_client.cancel_all_pending_orders()
                )
        
        # Should be fast due to parallel execution
        cancel_time = metrics.get_average_time("parallel_cancel_orders")
        assert cancel_time < 2.0, f"Parallel cancellation too slow: {cancel_time:.3f}s"
    
    async def test_parallel_position_closure(self, mock_client):
        """Test parallel position closure speed"""
        print("\n=== Testing Parallel Position Closure ===")
        
        # Mock positions
        mock_positions = [
            {
                "positionId": f"pos_{i}",
                "contractId": 12345,
                "netPos": 2 if i % 2 == 0 else -2,
                "timestamp": int(time.time())
            }
            for i in range(3)
        ]
        
        with patch.object(mock_client, 'get_positions', return_value=mock_positions):
            with patch.object(mock_client, '_fast_close_position', return_value={"orderId": "test_order"}):
                result = await time_operation(
                    "parallel_close_positions",
                    mock_client.force_close_all_positions_immediately()
                )
        
        # Should be fast due to parallel execution
        closure_time = metrics.get_average_time("parallel_close_positions")
        assert closure_time < 3.0, f"Parallel position closure too slow: {closure_time:.3f}s"
    
    async def test_fast_order_monitoring(self, mock_client):
        """Test the fast order monitoring system"""
        print("\n=== Testing Fast Order Monitoring ===")
        
        # Mock order that completes quickly
        mock_order = {"orderId": "test_order", "orderStatus": "Working"}
        completed_order = {"orderId": "test_order", "orderStatus": "Filled"}
        
        call_count = 0
        async def mock_get_order_status(order_id):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:  # Complete after 3 checks
                return completed_order
            return mock_order
        
        with patch.object(mock_client, 'get_order_status', side_effect=mock_get_order_status):
            result = await time_operation(
                "fast_order_monitoring",
                mock_client.monitor_all_orders_fast([mock_order], timeout=10)
            )
        
        # Should complete quickly with dynamic polling
        monitor_time = metrics.get_average_time("fast_order_monitoring")
        assert monitor_time < 5.0, f"Order monitoring too slow: {monitor_time:.3f}s"
        assert call_count >= 3, "Order monitoring should have made multiple checks"
    
    async def test_webhook_end_to_end_speed(self, sample_webhook_data):
        """Test complete webhook processing speed"""
        print("\n=== Testing Complete Webhook Speed ===")
        
        # Mock all external dependencies
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
            
            # Test webhook processing
            result = await time_operation(
                "complete_webhook_processing",
                webhook_optimized(sample_webhook_data)
            )
        
        # Should complete quickly
        webhook_time = metrics.get_average_time("complete_webhook_processing")
        assert webhook_time < 10.0, f"Complete webhook processing too slow: {webhook_time:.3f}s"
        
        # Verify response structure
        assert "status" in result
        assert "execution_time" in result
        assert result["status"] == "success"
    
    async def test_duplicate_detection_speed(self, sample_webhook_data):
        """Test optimized duplicate detection"""
        print("\n=== Testing Duplicate Detection Speed ===")
        
        from main import is_duplicate_request
        
        # Test duplicate detection speed
        start_time = time.time()
        
        # First request - not duplicate
        is_dup_1 = await time_operation("duplicate_check_1", is_duplicate_request(sample_webhook_data))
        assert not is_dup_1, "First request should not be duplicate"
        
        # Second request immediately - should be duplicate
        is_dup_2 = await time_operation("duplicate_check_2", is_duplicate_request(sample_webhook_data))
        assert is_dup_2, "Immediate second request should be duplicate"
        
        # Check speed
        avg_duplicate_time = (metrics.get_average_time("duplicate_check_1") + 
                            metrics.get_average_time("duplicate_check_2")) / 2
        assert avg_duplicate_time < 0.01, f"Duplicate detection too slow: {avg_duplicate_time:.3f}s"
    
    def test_configuration_optimizations(self):
        """Test that configuration optimizations are applied"""
        print("\n=== Testing Configuration Optimizations ===")
        
        # Test HTTP client configuration
        client = TradovateClient("test", "test", "test")
        http_client = client._get_http_client()
        
        # Verify optimized timeouts
        assert http_client.timeout.connect == 3.0, "Connect timeout should be 3.0s"
        assert http_client.timeout.read == 8.0, "Read timeout should be 8.0s"
        assert http_client.timeout.write == 3.0, "Write timeout should be 3.0s"
        
        # Verify connection limits
        limits = http_client.limits
        assert limits.max_keepalive_connections == 15, "Should have 15 keepalive connections"
        assert limits.max_connections == 30, "Should have 30 max connections"
        
        print("✓ HTTP client configuration optimized")

async def run_performance_benchmark():
    """Run a comprehensive performance benchmark"""
    print("\n" + "="*50)
    print("TRADOVATE WEBHOOK SPEED OPTIMIZATION BENCHMARK")
    print("="*50)
    
    # Initialize test client
    client = TradovateClient("test", "test", "test")
    
    # Mock authentication
    client.auth_token = "test_token"
    client.auth_expires_at = time.time() + 3600
    
    # Create mock HTTP client
    mock_http = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"success": True}
    mock_http.post.return_value = mock_response
    mock_http.get.return_value = mock_response
    mock_http.delete.return_value = mock_response
    client._http_client = mock_http
    
    # Test scenarios
    test_scenarios = [
        ("HTTP Connection Pool", test_http_connection_speed(client)),
        ("Parallel Order Operations", test_parallel_operations(client)),
        ("Dynamic Polling System", test_dynamic_polling(client)),
        ("Complete Webhook Flow", test_complete_flow(client)),
    ]
    
    for scenario_name, test_coro in test_scenarios:
        print(f"\n--- {scenario_name} ---")
        try:
            await time_operation(scenario_name, test_coro)
        except Exception as e:
            print(f"✗ {scenario_name} failed: {e}")
    
    # Print final metrics
    metrics.print_summary()

async def test_http_connection_speed(client):
    """Test HTTP connection speed with pooling"""
    tasks = []
    for i in range(20):
        task = client._make_authenticated_request("GET", f"/test/{i}")
        tasks.append(task)
    
    await asyncio.gather(*tasks, return_exceptions=True)

async def test_parallel_operations(client):
    """Test parallel order operations"""
    # Mock some orders and positions
    mock_orders = [{"orderId": f"order_{i}", "orderStatus": "Working"} for i in range(5)]
    mock_positions = [{"positionId": f"pos_{i}", "netPos": 1} for i in range(3)]
    
    with patch.object(client, 'get_pending_orders', return_value=mock_orders):
        with patch.object(client, 'get_positions', return_value=mock_positions):
            with patch.object(client, '_cancel_order_fast', return_value=True):
                with patch.object(client, '_fast_close_position', return_value={"orderId": "close_order"}):
                    # Test parallel cancellation and closure
                    cancel_task = client.cancel_all_pending_orders()
                    close_task = client.force_close_all_positions_immediately()
                    
                    await asyncio.gather(cancel_task, close_task)

async def test_dynamic_polling(client):
    """Test dynamic polling system"""
    mock_order = {"orderId": "test_order", "orderStatus": "Working"}
    
    call_count = 0
    async def mock_get_order_status(order_id):
        nonlocal call_count
        call_count += 1
        if call_count >= 5:
            return {"orderId": "test_order", "orderStatus": "Filled"}
        return mock_order
    
    with patch.object(client, 'get_order_status', side_effect=mock_get_order_status):
        await client.monitor_all_orders_fast([mock_order], timeout=10)

async def test_complete_flow(client):
    """Test complete optimized webhook flow"""
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
    
    with patch('main.TradovateClient', return_value=client):
        with patch.object(client, 'get_positions', return_value=[]):
            with patch.object(client, 'get_pending_orders', return_value=[]):
                with patch.object(client, 'cancel_all_pending_orders', return_value=[]):
                with patch.object(client, 'force_close_all_positions_immediately', return_value=[]):
                    with patch.object(client, 'determine_optimal_order_type', return_value="Market"):
                        with patch.object(client, 'place_order', return_value={"orderId": "test", "orderStatus": "Working"}):
                            with patch.object(client, 'monitor_all_orders_fast', return_value=[{"orderId": "test", "orderStatus": "Filled"}]):
                                await webhook_optimized(webhook_data)

if __name__ == "__main__":
    print("Starting Tradovate Webhook Speed Optimization Tests...")
    
    # Run the comprehensive benchmark
    asyncio.run(run_performance_benchmark())
    
    print("\n" + "="*50)
    print("SPEED OPTIMIZATION TESTING COMPLETE")
    print("="*50)
    
    # Run pytest for detailed testing
    print("\nRunning detailed pytest suite...")
    pytest.main([__file__, "-v", "--tb=short"])
