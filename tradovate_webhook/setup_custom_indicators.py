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
    
    print("🚀 Setting up custom Higher Timeframe TimeSync forward testing configurations...")
    
    # Clear any existing configurations first
    test_manager.configurations.clear()
    test_manager.stats.clear()
    test_manager.trades.clear()
    
    # Define the 20 test variations based on your specifications
    test_variations = [
        # Config 1-4: Sequential scaling
        {"multiplier": 2, "period": 4, "id": "HTF_2_TSM_4_V1", "name": "HTF-2 TSM-4 (Var 1)"},
        {"multiplier": 3, "period": 6, "id": "HTF_3_TSM_6_V2", "name": "HTF-3 TSM-6 (Var 2)"},
        {"multiplier": 4, "period": 8, "id": "HTF_4_TSM_8_V3", "name": "HTF-4 TSM-8 (Var 3)"},
        {"multiplier": 5, "period": 10, "id": "HTF_5_TSM_10_V4", "name": "HTF-5 TSM-10 (Var 4)"},
        
        # Config 5-8: Cross combinations
        {"multiplier": 2, "period": 10, "id": "HTF_2_TSM_10_V5", "name": "HTF-2 TSM-10 (Var 5)"},
        {"multiplier": 3, "period": 8, "id": "HTF_3_TSM_8_V6", "name": "HTF-3 TSM-8 (Var 6)"},
        {"multiplier": 4, "period": 6, "id": "HTF_4_TSM_6_V7", "name": "HTF-4 TSM-6 (Var 7)"},
        {"multiplier": 5, "period": 4, "id": "HTF_5_TSM_4_V8", "name": "HTF-5 TSM-4 (Var 8)"},
        
        # Config 9-12: Mid-range combinations
        {"multiplier": 2, "period": 5, "id": "HTF_2_TSM_5_V9", "name": "HTF-2 TSM-5 (Var 9)"},
        {"multiplier": 3, "period": 4, "id": "HTF_3_TSM_4_V10", "name": "HTF-3 TSM-4 (Var 10)"},
        {"multiplier": 4, "period": 5, "id": "HTF_4_TSM_5_V11", "name": "HTF-4 TSM-5 (Var 11)"},
        {"multiplier": 5, "period": 6, "id": "HTF_5_TSM_6_V12", "name": "HTF-5 TSM-6 (Var 12)"},
        
        # Config 13-16: Additional variations
        {"multiplier": 5, "period": 3, "id": "HTF_5_TSM_3_V13", "name": "HTF-5 TSM-3 (Var 13)"},
        {"multiplier": 3, "period": 10, "id": "HTF_3_TSM_10_V14", "name": "HTF-3 TSM-10 (Var 14)"},
        {"multiplier": 4, "period": 10, "id": "HTF_4_TSM_10_V15", "name": "HTF-4 TSM-10 (Var 15)"},
        {"multiplier": 2, "period": 6, "id": "HTF_2_TSM_6_V16", "name": "HTF-2 TSM-6 (Var 16)"},
        
        # Config 17-20: M5-P5 repetitions for statistical significance
        {"multiplier": 5, "period": 5, "id": "HTF_5_TSM_5_V17", "name": "HTF-5 TSM-5 (Var 17)"},
        {"multiplier": 5, "period": 5, "id": "HTF_5_TSM_5_V18", "name": "HTF-5 TSM-5 (Var 18)"},
        {"multiplier": 5, "period": 5, "id": "HTF_5_TSM_5_V19", "name": "HTF-5 TSM-5 (Var 19)"},
        {"multiplier": 5, "period": 5, "id": "HTF_5_TSM_5_V20", "name": "HTF-5 TSM-5 (Var 20)"},
    ]
    
    # Register each test configuration
    for i, config in enumerate(test_variations, 1):
        config_id = config["id"]
        name = config["name"]
        multiplier = config["multiplier"]
        period = config["period"]
        
        description = f"Higher Timeframe Multiplier: {multiplier}, TimeSeries Model Period: {period}"
        
        test_manager.register_test_config(
            config_id=config_id,
            name=name,
            description=description,
            settings={
                "higher_timeframe_multiplier": multiplier,
                "timeseries_model_period": period,
                "timeseries_model_type": "ZEMA",  # Fixed as shown in image
                "variation_number": i
            }
        )
        
        logging.info(f"Registered test config {i}: {config_id} - {name}")
    
    return len(test_variations)

def generate_alert_templates():
    """
    Generate TradingView alert templates for each configuration
    """
    print("\n📨 TradingView Alert Templates:")
    print("=" * 80)
    
    for config_id, config in test_manager.configurations.items():
        settings = config["settings"]
        multiplier = settings["higher_timeframe_multiplier"]
        period = settings["timeseries_model_period"]
        var_num = settings["variation_number"]
        
        print(f"\n🔧 {config['name']} ({config_id}):")
        print("-" * 50)
        
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
        print(f"   • TimeSeries Model Type: ZEMA (keep as shown in image)")
        print(f"2. Create an alert using the template above")
        print(f"3. Set webhook URL to: http://your-server.com/webhook")
        print(f"4. The config_id '{config_id}' will track variation #{var_num} separately")

def show_performance_dashboard():
    """
    Show current performance status
    """
    print(f"\n🏆 Current Performance Leaderboard:")
    print("=" * 80)
    
    if not test_manager.stats or not any(stats.total_trades > 0 for stats in test_manager.stats.values()):
        print("No results yet. Start sending alerts to see performance data!")
        return
    
    # Sort configs by performance metrics
    sorted_configs = sorted(
        test_manager.stats.items(),
        key=lambda x: x[1].total_pnl,
        reverse=True
    )
    
    print(f"{'Rank':<4} {'Configuration':<35} {'P&L':<12} {'Trades':<8} {'Win Rate':<10}")
    print("-" * 80)
    
    for i, (config_id, stats) in enumerate(sorted_configs[:10], 1):
        config = test_manager.configurations.get(config_id)
        if config and stats.total_trades > 0:
            name = config['name'][:30]
            pnl = stats.total_pnl
            trades = stats.total_trades
            win_rate = stats.win_rate
            
            print(f"{i:<4} {name:<35} ${pnl:>8.2f} {trades:>6} {win_rate:>7.1f}%")

if __name__ == "__main__":
    print("🚀 Setting up custom Higher Timeframe TimeSync forward testing configurations...")
    
    # Setup the configurations
    num_configs = setup_custom_test_configurations()
    print(f"✅ Registered {num_configs} test configurations")
    
    # Show active configurations
    print(f"\n📋 Active Test Configurations:")
    for config_id, config in test_manager.configurations.items():
        settings = config["settings"]
        multiplier = settings["higher_timeframe_multiplier"]
        period = settings["timeseries_model_period"]
        var_num = settings["variation_number"]
        print(f"  • {config_id}: {config['name']} - Multiplier: {multiplier}, Period: {period}")
    
    # Generate alert templates
    generate_alert_templates()
    
    # Show performance dashboard
    show_performance_dashboard()
    
    print(f"\n🎯 Next Steps:")
    print("1. Set up TradingView alerts using the templates above")
    print("2. Use the config_id in each alert to track variations separately")
    print("3. Monitor performance using these API endpoints:")
    print("   • GET /forward-test/status - Overall status")
    print("   • GET /forward-test/leaderboard - Performance rankings")
    print("   • GET /forward-test/report/{config_id} - Detailed config report")
    print("   • GET /forward-test/export - Export all data to CSV")
