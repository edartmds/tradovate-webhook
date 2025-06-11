from forward_test_manager import test_manager

# ==============================================================================
# 🚀 FORWARD TESTING CONFIGURATION FOR TRADINGVIEW INDICATORS
# ==============================================================================

# 📊 Example: Multiple MACD configurations to test
MACD_CONFIGS = [
    {
        'config_id': 'MACD_12_26_9',
        'name': 'MACD Standard',
        'description': 'Standard MACD with 12,26,9 settings',
        'parameters': {'fast': 12, 'slow': 26, 'signal': 9}
    },
    {
        'config_id': 'MACD_8_21_5',
        'name': 'MACD Fast',
        'description': 'Faster MACD with 8,21,5 settings',
        'parameters': {'fast': 8, 'slow': 21, 'signal': 5}
    },
    {
        'config_id': 'MACD_19_39_9',
        'name': 'MACD Slow',
        'description': 'Slower MACD with 19,39,9 settings',
        'parameters': {'fast': 19, 'slow': 39, 'signal': 9}
    },
    {
        'config_id': 'MACD_10_30_7',
        'name': 'MACD Custom1',
        'description': 'Custom MACD with 10,30,7 settings',
        'parameters': {'fast': 10, 'slow': 30, 'signal': 7}
    },
    {
        'config_id': 'MACD_15_35_10',
        'name': 'MACD Custom2',
        'description': 'Custom MACD with 15,35,10 settings',
        'parameters': {'fast': 15, 'slow': 35, 'signal': 10}
    }
]

# 📈 Example: RSI configurations to test
RSI_CONFIGS = [
    {
        'config_id': 'RSI_14_30_70',
        'name': 'RSI Standard',
        'description': 'Standard RSI with 14 period, 30/70 levels',
        'parameters': {'period': 14, 'oversold': 30, 'overbought': 70}
    },
    {
        'config_id': 'RSI_9_25_75',
        'name': 'RSI Fast',
        'description': 'Fast RSI with 9 period, 25/75 levels',
        'parameters': {'period': 9, 'oversold': 25, 'overbought': 75}
    },
    {
        'config_id': 'RSI_21_35_65',
        'name': 'RSI Slow',
        'description': 'Slow RSI with 21 period, 35/65 levels',
        'parameters': {'period': 21, 'oversold': 35, 'overbought': 65}
    }
]

# 🔧 Example: Bollinger Band configurations
BB_CONFIGS = [
    {
        'config_id': 'BB_20_2.0',
        'name': 'BB Standard',
        'description': 'Standard BB with 20 period, 2.0 deviation',
        'parameters': {'period': 20, 'deviation': 2.0}
    },
    {
        'config_id': 'BB_10_1.5',
        'name': 'BB Tight',
        'description': 'Tight BB with 10 period, 1.5 deviation',
        'parameters': {'period': 10, 'deviation': 1.5}
    },
    {
        'config_id': 'BB_30_2.5',
        'name': 'BB Wide',
        'description': 'Wide BB with 30 period, 2.5 deviation',
        'parameters': {'period': 30, 'deviation': 2.5}
    }
]

def setup_all_test_configs():
    """Register all test configurations with the manager"""
    
    print("🚀 Setting up forward testing configurations...")
    
    # Register MACD configs
    for config in MACD_CONFIGS:
        test_manager.register_test_config(config['config_id'], config)
    
    # Register RSI configs
    for config in RSI_CONFIGS:
        test_manager.register_test_config(config['config_id'], config)
    
    # Register Bollinger Band configs
    for config in BB_CONFIGS:
        test_manager.register_test_config(config['config_id'], config)
    
    print(f"✅ Registered {len(MACD_CONFIGS + RSI_CONFIGS + BB_CONFIGS)} test configurations")
    print("\n📋 Active Test Configurations:")
    
    for config_id, config in test_manager.test_configs.items():
        print(f"  • {config_id}: {config['name']} - {config['description']}")

def create_tradingview_alert_template(config_id: str, symbol: str = "NQ1!"):
    """
    Generate TradingView alert message template for a specific configuration
    
    Use this template in your TradingView alerts for each indicator variation
    """
    
    template = f"""
{{
  "symbol": "{symbol}",
  "action": "{{{{ strategy.order.action }}}}",
  "PRICE": "{{{{ close }}}}",
  "T1": "{{{{ strategy.order.contracts }}}}",
  "STOP": "{{{{ strategy.position_avg_price }}}}",
  "config_id": "{config_id}",
  "timestamp": "{{{{ time }}}}"
}}
"""
    
    return template.strip()

def generate_all_alert_templates():
    """Generate alert templates for all configurations"""
    
    print("\n📨 TradingView Alert Templates:")
    print("="*60)
    
    all_configs = MACD_CONFIGS + RSI_CONFIGS + BB_CONFIGS
    
    for config in all_configs:
        config_id = config['config_id']
        print(f"\n🔧 {config['name']} ({config_id}):")
        print("-" * 40)
        print(create_tradingview_alert_template(config_id))
        print("\n💡 Instructions:")
        print(f"1. Apply your indicator with these settings: {config['parameters']}")
        print(f"2. Create an alert using the template above")
        print(f"3. Set webhook URL to: http://your-server.com/webhook")
        print(f"4. The config_id '{config_id}' will track this variation separately")

if __name__ == "__main__":
    # Setup all configurations
    setup_all_test_configs()
    
    # Generate alert templates
    generate_all_alert_templates()
    
    # Show current leaderboard
    print("\n🏆 Current Performance Leaderboard:")
    print("="*60)
    leaderboard = test_manager.get_leaderboard()
    
    if leaderboard:
        for i, config in enumerate(leaderboard, 1):
            print(f"{i}. {config['name']} ({config['config_id']})")
            print(f"   Trades: {config['total_trades']}, Win Rate: {config['win_rate']:.1f}%")
            print(f"   Total PnL: {config['total_pnl']:.2f}, Profit Factor: {config['profit_factor']:.2f}")
            print()
    else:
        print("No results yet. Start sending alerts to see performance data!")
