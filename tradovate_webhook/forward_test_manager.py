import asyncio
import json
import pandas as pd
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
import logging
import os
from typing import Dict, List, Optional
import hashlib

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("forward_test.log"),
        logging.StreamHandler()
    ]
)

class ForwardTestManager:
    def export_leaderboard(self, filename: str = "forward_test_leaderboard.csv"):
        """Export the leaderboard summary to a CSV file for easy review of all variations."""
        leaderboard = self.get_leaderboard()
        if not leaderboard:
            logging.info("No leaderboard data to export.")
            return None
        df = pd.DataFrame(leaderboard)
        df.to_csv(filename, index=False)
        logging.info(f"Exported leaderboard to {filename}")
        return filename
    """Manages multiple indicator variations for forward testing"""
    
    def __init__(self):
        self.test_configs = {}  # {config_id: config_data}
        self.test_results = {}  # {config_id: {trades: [], metrics: {}}}
        self.active_positions = {}  # {config_id: position_data}
        self.data_file = "forward_test_data.json"
        self.load_existing_data()
    
    def load_existing_data(self):
        """Load existing test data from file"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r') as f:
                    data = json.load(f)
                    self.test_configs = data.get('configs', {})
                    self.test_results = data.get('results', {})
                    self.active_positions = data.get('positions', {})
                logging.info(f"Loaded {len(self.test_configs)} test configurations")
        except Exception as e:
            logging.error(f"Error loading data: {e}")
    
    def save_data(self):
        """Save test data to file"""
        try:
            data = {
                'configs': self.test_configs,
                'results': self.test_results,
                'positions': self.active_positions
            }
            with open(self.data_file, 'w') as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logging.error(f"Error saving data: {e}")
    
    def register_test_config(self, config_id: str, config_data: dict):
        """Register a new test configuration"""
        self.test_configs[config_id] = {
            'name': config_data.get('name', f'Config_{config_id}'),
            'description': config_data.get('description', ''),
            'parameters': config_data.get('parameters', {}),
            'start_time': datetime.now().isoformat(),
            'status': 'active'
        }
        
        if config_id not in self.test_results:
            self.test_results[config_id] = {
                'trades': [],
                'metrics': {
                    'total_trades': 0,
                    'winning_trades': 0,
                    'losing_trades': 0,
                    'total_pnl': 0.0,
                    'win_rate': 0.0,
                    'avg_win': 0.0,
                    'avg_loss': 0.0,
                    'profit_factor': 0.0,
                    'max_drawdown': 0.0
                }
            }
        
        logging.info(f"Registered test config: {config_id} - {config_data.get('name')}")
        self.save_data()
    
    def process_signal(self, signal_data: dict):
        """Process incoming signal and determine which test config it belongs to"""
        config_id = signal_data.get('config_id') or signal_data.get('input_id')
        
        if not config_id:
            # Try to extract from alert message or use default
            config_id = self.extract_config_from_signal(signal_data)
        
        if config_id not in self.test_configs:
            logging.warning(f"Unknown config_id: {config_id}. Creating default config.")
            self.register_test_config(config_id, {
                'name': f'Auto_{config_id}',
                'description': 'Auto-created from signal',
                'parameters': signal_data
            })
        
        # Close any existing position for this config
        if config_id in self.active_positions:
            self.close_position(config_id, signal_data)
        
        # Open new position
        self.open_position(config_id, signal_data)
    
    def extract_config_from_signal(self, signal_data: dict) -> str:
        """Extract config ID from signal data"""
        # Try various fields that might contain config info
        possible_fields = ['input_id', 'strategy_name', 'config', 'version']
        
        for field in possible_fields:
            if field in signal_data:
                return str(signal_data[field])
        
        # Generate based on signal parameters
        params = {
            'symbol': signal_data.get('symbol'),
            'action': signal_data.get('action'),
            'price_range': f"{signal_data.get('PRICE', 0):.2f}"
        }
        config_string = json.dumps(params, sort_keys=True)
        return hashlib.md5(config_string.encode()).hexdigest()[:8]
    
    def open_position(self, config_id: str, signal_data: dict):
        """Open a new position for the config"""
        position = {
            'entry_time': datetime.now().isoformat(),
            'entry_price': signal_data.get('PRICE', 0),
            'action': signal_data.get('action', '').lower(),
            'target_price': signal_data.get('T1', 0),
            'stop_price': signal_data.get('STOP', 0),
            'symbol': signal_data.get('symbol', ''),
            'quantity': 1
        }
        
        self.active_positions[config_id] = position
        logging.info(f"Opened position for {config_id}: {position['action']} at {position['entry_price']}")
        self.save_data()
    
    def close_position(self, config_id: str, exit_data: dict = None):
        """Close an existing position and record the trade"""
        if config_id not in self.active_positions:
            return
        
        position = self.active_positions[config_id]
        exit_time = datetime.now().isoformat()
        exit_price = exit_data.get('exit_price') if exit_data else position['target_price']
        
        # Calculate PnL
        if position['action'] == 'buy':
            pnl = exit_price - position['entry_price']
        else:
            pnl = position['entry_price'] - exit_price
        
        # Record the trade
        trade = {
            'entry_time': position['entry_time'],
            'exit_time': exit_time,
            'entry_price': position['entry_price'],
            'exit_price': exit_price,
            'action': position['action'],
            'pnl': pnl,
            'symbol': position['symbol'],
            'duration_minutes': self.calculate_duration(position['entry_time'], exit_time)
        }
        
        self.test_results[config_id]['trades'].append(trade)
        self.update_metrics(config_id)
        
        # Remove from active positions
        del self.active_positions[config_id]
        
        logging.info(f"Closed position for {config_id}: PnL = {pnl:.2f}")
        self.save_data()
    
    def calculate_duration(self, start_time: str, end_time: str) -> float:
        """Calculate duration between two times in minutes"""
        start = datetime.fromisoformat(start_time.replace('Z', '+00:00').replace('+00:00', ''))
        end = datetime.fromisoformat(end_time.replace('Z', '+00:00').replace('+00:00', ''))
        return (end - start).total_seconds() / 60
    
    def update_metrics(self, config_id: str):
        """Update performance metrics for a config"""
        trades = self.test_results[config_id]['trades']
        metrics = self.test_results[config_id]['metrics']
        
        if not trades:
            return
        
        # Basic metrics
        metrics['total_trades'] = len(trades)
        winning_trades = [t for t in trades if t['pnl'] > 0]
        losing_trades = [t for t in trades if t['pnl'] < 0]
        
        metrics['winning_trades'] = len(winning_trades)
        metrics['losing_trades'] = len(losing_trades)
        metrics['total_pnl'] = sum(t['pnl'] for t in trades)
        metrics['win_rate'] = len(winning_trades) / len(trades) * 100 if trades else 0
        
        # Average win/loss
        metrics['avg_win'] = sum(t['pnl'] for t in winning_trades) / len(winning_trades) if winning_trades else 0
        metrics['avg_loss'] = sum(t['pnl'] for t in losing_trades) / len(losing_trades) if losing_trades else 0
        
        # Profit factor
        gross_profit = sum(t['pnl'] for t in winning_trades)
        gross_loss = abs(sum(t['pnl'] for t in losing_trades))
        metrics['profit_factor'] = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # Max drawdown
        cumulative_pnl = 0
        peak = 0
        max_dd = 0
        for trade in trades:
            cumulative_pnl += trade['pnl']
            if cumulative_pnl > peak:
                peak = cumulative_pnl
            drawdown = peak - cumulative_pnl
            if drawdown > max_dd:
                max_dd = drawdown
        metrics['max_drawdown'] = max_dd
    
    def get_leaderboard(self) -> List[dict]:
        """Get ranked performance of all configs"""
        leaderboard = []
        
        for config_id, config in self.test_configs.items():
            metrics = self.test_results[config_id]['metrics']
            
            if metrics['total_trades'] >= 5:  # Only include configs with meaningful data
                leaderboard.append({
                    'config_id': config_id,
                    'name': config['name'],
                    'total_trades': metrics['total_trades'],
                    'win_rate': metrics['win_rate'],
                    'total_pnl': metrics['total_pnl'],
                    'profit_factor': metrics['profit_factor'],
                    'max_drawdown': metrics['max_drawdown'],
                    'avg_win': metrics['avg_win'],
                    'avg_loss': metrics['avg_loss']
                })
        
        # Sort by total PnL descending
        leaderboard.sort(key=lambda x: x['total_pnl'], reverse=True)
        return leaderboard
    
    def export_results(self, filename: str = None):
        """Export results to CSV"""
        if not filename:
            filename = f"forward_test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        all_trades = []
        for config_id, results in self.test_results.items():
            config_name = self.test_configs.get(config_id, {}).get('name', config_id)
            for trade in results['trades']:
                trade_record = trade.copy()
                trade_record['config_id'] = config_id
                trade_record['config_name'] = config_name
                all_trades.append(trade_record)
        
        df = pd.DataFrame(all_trades)
        df.to_csv(filename, index=False)
        logging.info(f"Exported {len(all_trades)} trades to {filename}")
        return filename

# Global instance
test_manager = ForwardTestManager()
