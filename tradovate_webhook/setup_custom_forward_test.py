#!/usr/bin/env python3
"""
🔥 CUSTOM FORWARD TESTING SETUP FOR HIGHER TIMEFRAME TIMESYNC INDICATOR
Setup forward testing configurations for the specific indicator variations
"""

import logging
import json
from enhanced_forward_test_manager import test_manager

def setup_custom_test_configurations():
    """
    Setup the 20 specific test configurations for the Higher Timeframe TimeSync indicator
    """
    
    # Clear any existing configurations first
    test_manager.configurations.clear()
    
    # Define the 20 test variations based on your specifications
    test_variations = [
        # Config 1-4: Sequential scaling
        {"multiplier": 2, "period": 4, "id": "HT_M2_P4", "name": "TimeSync M2-P4"},
        {"multiplier": 3, "period": 6, "id": "HT_M3_P6", "name": "TimeSync M3-P6"},
        {"multiplier": 4, "period": 8, "id": "HT_M4_P8", "name": "TimeSync M4-P8"},
        {"multiplier": 5, "period": 10, "id": "HT_M5_P10", "name": "TimeSync M5-P10"},
        
        # Config 5-8: Cross combinations
        {"multiplier": 2, "period": 10, "id": "HT_M2_P10", "name": "TimeSync M2-P10"},
        {"multiplier": 3, "period": 8, "id": "HT_M3_P8", "name": "TimeSync M3-P8"},
        {"multiplier": 4, "period": 6, "id": "HT_M4_P6", "name": "TimeSync M4-P6"},
        {"multiplier": 5, "period": 4, "id": "HT_M5_P4", "name": "TimeSync M5-P4"},
        
        # Config 9-12: Mid-range combinations
        {"multiplier": 2, "period": 5, "id": "HT_M2_P5", "name": "TimeSync M2-P5"},
        {"multiplier": 3, "period": 4, "id": "HT_M3_P4", "name": "TimeSync M3-P4"},
        {"multiplier": 4, "period": 5, "id": "HT_M4_P5", "name": "TimeSync M4-P5"},
        {"multiplier": 5, "period": 6, "id": "HT_M5_P6", "name": "TimeSync M5-P6"},
        
        # Config 13-16: Additional variations
        {"multiplier": 5, "period": 3, "id": "HT_M5_P3", "name": "TimeSync M5-P3"},
        {"multiplier": 3, "period": 10, "id": "HT_M3_P10", "name": "TimeSync M3-P10"},
        {"multiplier": 4, "period": 10, "id": "HT_M4_P10", "name": "TimeSync M4-P10"},
        {"multiplier": 2, "period": 6, "id": "HT_M2_P6", "name": "TimeSync M2-P6"},
        
        # Config 17-20: M5-P5 repetitions for statistical significance
        {"multiplier": 5, "period": 5, "id": "HT_M5_P5_A", "name": "TimeSync M5-P5 (Test A)"},
        {"multiplier": 5, "period": 5, "id": "HT_M5_P5_B", "name": "TimeSync M5-P5 (Test B)"},
        {"multiplier": 5, "period": 5, "id": "HT_M5_P5_C", "name": "TimeSync M5-P5 (Test C)"},
        {"multiplier": 5, "period": 5, "id": "HT_M5_P5_D", "name": "TimeSync M5-P5 (Test D)"},
    ]
    
    # Register each test configuration
    for i, config in enumerate(test_variations, 1):
        config_id = config["id"]
        name = config["name"]
        multiplier = config["multiplier"]
        period = config["period"]
        
        description = f"Higher Timeframe TimeSync with Multiplier={multiplier}, Period={period}"
        test_manager.register_test_config(
            config_id=config_id,
            name=name,
            description=description,
            settings={
                "higher_timeframe_multiplier": multiplier,
                "timeseries_model_period": period,
                "timeseries_model_type": "ZEMA"  # Fixed as shown in image
            }
        )
        
        logging.info(f"Registered test config {i}: {config_id} - {name}")
    
    return len(test_variations)

def generate_alert_templates():
    """
    Generate TradingView alert templates for each configuration
    """
    print("\n📨 TradingView Alert Templates:")
    print("=" * 60)
    
    for config_id, config in test_manager.configurations.items():
        settings = config["settings"]
        multiplier = settings["higher_timeframe_multiplier"]
        period = settings["timeseries_model_period"]
        
        print(f"\n🔧 {config['name']} ({config_id}):")
        print("-" * 40)
        
        # Alert template with the config_id
        template = {
            "symbol": "NQ1!",
            "action": "{{ strategy.order.action }}",
            "PRICE": "{{ close }}",
            "T1": "{{ strategy.order.contracts }}",
            "STOP": "{{ strategy.position_avg_price }}",
            "config_id": config_id,
            "timestamp": "{{ time }}"
        }
        
        print(json.dumps(template, indent=2))
        
        print(f"\n💡 Instructions:")
        print(f"1. Apply your Higher Timeframe TimeSync indicator with these settings:")
        print(f"   • Higher Timeframe TimeSync Multiplier: {multiplier}")
        print(f"   • TimeSeries Model Period: {period}")
        print(f"   • TimeSeries Model Type: ZEMA (keep as shown)")
        print(f"2. Create an alert using the template above")
        print(f"3. Set webhook URL to: http://your-server.com/webhook")
        print(f"4. The config_id '{config_id}' will track this variation separately")

def show_performance_dashboard():
    """
    Show current performance status
    """
    print(f"\n🏆 Current Performance Leaderboard:")
    print("=" * 60)
    
    if not test_manager.stats:
        print("No results yet. Start sending alerts to see performance data!")
        return
    
    # Sort configs by performance metrics
    sorted_configs = sorted(
        test_manager.stats.items(),
        key=lambda x: x[1].total_pnl,
        reverse=True
    )
    
    for i, (config_id, stats) in enumerate(sorted_configs[:10], 1):
        config = test_manager.configurations.get(config_id)
        if config:
            pnl = stats.total_pnl
            trades = stats.total_trades
            win_rate = stats.win_rate
            
            print(f"{i:2d}. {config['name'][:30]:30} | "
                  f"PnL: ${pnl:8.2f} | "
                  f"Trades: {trades:3d} | "
                  f"Win Rate: {win_rate:5.1f}%")

if __name__ == "__main__":
    print("🚀 Setting up custom Higher Timeframe TimeSync forward testing configurations...")
    
    # Setup the configurations
    num_configs = setup_custom_test_configurations()
    print(f"✅ Registered {num_configs} test configurations")
    
    # Show active configurations
    print(f"\n📋 Active Test Configurations:")
    for config_id, config in test_manager.configurations.items():
        settings = config["settings"]
        print(f"  • {config_id}: {config['name']} - {config['description']}")
        print(f"    Multiplier: {settings['higher_timeframe_multiplier']}, Period: {settings['timeseries_model_period']}")
    
    # Generate alert templates
    generate_alert_templates()
    
    # Show performance dashboard
    show_performance_dashboard()
