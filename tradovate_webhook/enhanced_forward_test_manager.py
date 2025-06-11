"""
Enhanced Forward Test Manager with Comprehensive Performance Tracking
Tracks all the detailed metrics requested for each indicator variation
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import statistics
import pandas as pd

@dataclass
class TradeRecord:
    """Individual trade record with all necessary data"""
    config_id: str
    entry_time: datetime
    exit_time: Optional[datetime]
    entry_price: float
    exit_price: Optional[float]
    action: str  # "BUY" or "SELL"
    symbol: str
    take_profit: float
    stop_loss: float
    status: str  # "OPEN", "WIN", "LOSS", "BREAKEVEN"
    pnl: float = 0.0
    contracts: int = 1
    trade_duration_minutes: int = 0
    fees_commission: float = 2.50  # Typical futures commission
    max_runup: float = 0.0
    max_drawdown: float = 0.0

@dataclass
class ConfigurationStats:
    """Comprehensive statistics for each configuration"""
    config_id: str
    name: str
    
    # All Trades
    total_trades: int = 0
    total_contracts: int = 0
    gross_pnl: float = 0.0
    total_pnl: float = 0.0
    trade_fees_commission: float = 0.0
    expectancy: float = 0.0
    avg_trade_time_minutes: float = 0.0
    longest_trade_time_minutes: int = 0
    percent_profitable: float = 0.0
    
    # Winning Trades
    total_profit: float = 0.0
    winning_trades: int = 0
    winning_contracts: int = 0
    largest_winning_trade: float = 0.0
    avg_winning_trade: float = 0.0
    std_dev_winning_trade: float = 0.0
    avg_winning_trade_time_minutes: float = 0.0
    longest_winning_trade_time_minutes: int = 0
    max_runup: float = 0.0
    
    # Losing Trades
    total_loss: float = 0.0
    losing_trades: int = 0
    losing_contracts: int = 0
    largest_losing_trade: float = 0.0
    avg_losing_trade: float = 0.0
    std_dev_losing_trade: float = 0.0
    avg_losing_trade_time_minutes: float = 0.0
    longest_losing_trade_time_minutes: int = 0
    max_drawdown: float = 0.0
    
    # Additional Performance Metrics
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    current_streak: int = 0
    current_streak_type: str = "NONE"  # "WIN", "LOSS", "NONE"

class EnhancedForwardTestManager:
    """Enhanced forward test manager with comprehensive tracking"""
    
    def __init__(self, data_dir: str = "forward_test_data"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        
        self.configurations: Dict[str, Dict] = {}
        self.trades: Dict[str, List[TradeRecord]] = {}  # config_id -> list of trades
        self.stats: Dict[str, ConfigurationStats] = {}  # config_id -> stats
        self.active_trades: Dict[str, TradeRecord] = {}  # config_id -> current open trade
        
        # Load existing data
        self._load_data()
        
        logging.info("Enhanced Forward Test Manager initialized")
    
    def register_test_config(self, config_id: str, name: str, description: str, settings: Dict):
        """Register a new test configuration"""
        self.configurations[config_id] = {
            "name": name,
            "description": description,
            "settings": settings,
            "created_at": datetime.now().isoformat()
        }
        
        # Initialize stats for this config
        if config_id not in self.stats:
            self.stats[config_id] = ConfigurationStats(config_id=config_id, name=name)
        
        # Initialize trades list
        if config_id not in self.trades:
            self.trades[config_id] = []
        
        self._save_data()
        logging.info(f"Registered test config: {config_id} - {name}")
    
    def process_signal(self, alert_data: Dict):
        """Process incoming trading signal and update tracking"""
        config_id = alert_data.get("config_id", "unknown")
        
        if config_id not in self.configurations:
            logging.warning(f"Unknown config_id: {config_id}")
            return
        
        symbol = alert_data.get("symbol", "NQ1!")
        action = alert_data.get("action", "").upper()
        price = float(alert_data.get("PRICE", 0))
        take_profit = float(alert_data.get("T1", 0))
        stop_loss = float(alert_data.get("STOP", 0))
        
        # Check if there's an existing open trade for this config
        if config_id in self.active_trades:
            # Close the existing trade
            self._close_trade(config_id, price, datetime.now())
        
        # Open new trade
        self._open_trade(config_id, action, symbol, price, take_profit, stop_loss)
        
        logging.info(f"Processed signal for {config_id}: {action} {symbol} @ {price}")
    
    def _open_trade(self, config_id: str, action: str, symbol: str, price: float, 
                   take_profit: float, stop_loss: float):
        """Open a new trade"""
        trade = TradeRecord(
            config_id=config_id,
            entry_time=datetime.now(),
            exit_time=None,
            entry_price=price,
            exit_price=None,
            action=action,
            symbol=symbol,
            take_profit=take_profit,
            stop_loss=stop_loss,
            status="OPEN"
        )
        
        self.active_trades[config_id] = trade
        self._save_data()
    
    def _close_trade(self, config_id: str, exit_price: float, exit_time: datetime):
        """Close an existing trade and calculate results"""
        if config_id not in self.active_trades:
            return
        
        trade = self.active_trades[config_id]
        trade.exit_time = exit_time
        trade.exit_price = exit_price
        trade.trade_duration_minutes = int((exit_time - trade.entry_time).total_seconds() / 60)
        
        # Calculate P&L
        if trade.action == "BUY":
            trade.pnl = (exit_price - trade.entry_price) * trade.contracts
        else:  # SELL
            trade.pnl = (trade.entry_price - exit_price) * trade.contracts
        
        # Determine trade outcome
        if trade.pnl > 0:
            trade.status = "WIN"
        elif trade.pnl < 0:
            trade.status = "LOSS"
        else:
            trade.status = "BREAKEVEN"
        
        # Subtract fees/commission
        trade.pnl -= trade.fees_commission
        
        # Add to completed trades
        self.trades[config_id].append(trade)
        
        # Remove from active trades
        del self.active_trades[config_id]
        
        # Update statistics
        self._update_stats(config_id)
        
        self._save_data()
        
        logging.info(f"Closed trade for {config_id}: {trade.status} ${trade.pnl:.2f}")
    
    def _update_stats(self, config_id: str):
        """Update comprehensive statistics for a configuration"""
        trades_list = self.trades[config_id]
        if not trades_list:
            return
        
        stats = self.stats[config_id]
        
        # Reset stats for recalculation
        stats.total_trades = len(trades_list)
        stats.total_contracts = sum(t.contracts for t in trades_list)
        stats.trade_fees_commission = sum(t.fees_commission for t in trades_list)
        
        # Calculate P&L metrics
        pnls = [t.pnl for t in trades_list]
        stats.gross_pnl = sum(pnls) + stats.trade_fees_commission
        stats.total_pnl = sum(pnls)
        
        if stats.total_trades > 0:
            stats.expectancy = stats.total_pnl / stats.total_trades
        
        # Trade duration metrics
        durations = [t.trade_duration_minutes for t in trades_list if t.trade_duration_minutes > 0]
        if durations:
            stats.avg_trade_time_minutes = statistics.mean(durations)
            stats.longest_trade_time_minutes = max(durations)
        
        # Winning trades analysis
        winning_trades = [t for t in trades_list if t.status == "WIN"]
        stats.winning_trades = len(winning_trades)
        stats.winning_contracts = sum(t.contracts for t in winning_trades)
        
        if winning_trades:
            winning_pnls = [t.pnl for t in winning_trades]
            stats.total_profit = sum(winning_pnls)
            stats.largest_winning_trade = max(winning_pnls)
            stats.avg_winning_trade = statistics.mean(winning_pnls)
            if len(winning_pnls) > 1:
                stats.std_dev_winning_trade = statistics.stdev(winning_pnls)
            
            winning_durations = [t.trade_duration_minutes for t in winning_trades if t.trade_duration_minutes > 0]
            if winning_durations:
                stats.avg_winning_trade_time_minutes = statistics.mean(winning_durations)
                stats.longest_winning_trade_time_minutes = max(winning_durations)
        
        # Losing trades analysis
        losing_trades = [t for t in trades_list if t.status == "LOSS"]
        stats.losing_trades = len(losing_trades)
        stats.losing_contracts = sum(t.contracts for t in losing_trades)
        
        if losing_trades:
            losing_pnls = [abs(t.pnl) for t in losing_trades]  # Use absolute values
            stats.total_loss = sum(losing_pnls)
            stats.largest_losing_trade = max(losing_pnls)
            stats.avg_losing_trade = statistics.mean(losing_pnls)
            if len(losing_pnls) > 1:
                stats.std_dev_losing_trade = statistics.stdev(losing_pnls)
            
            losing_durations = [t.trade_duration_minutes for t in losing_trades if t.trade_duration_minutes > 0]
            if losing_durations:
                stats.avg_losing_trade_time_minutes = statistics.mean(losing_durations)
                stats.longest_losing_trade_time_minutes = max(losing_durations)
        
        # Calculate ratios and percentages
        if stats.total_trades > 0:
            stats.percent_profitable = (stats.winning_trades / stats.total_trades) * 100
            stats.win_rate = stats.percent_profitable
        
        if stats.total_loss > 0:
            stats.profit_factor = stats.total_profit / stats.total_loss
        
        # Calculate consecutive streaks
        self._calculate_streaks(config_id)
        
        # Calculate drawdown and runup
        self._calculate_drawdown_runup(config_id)
    
    def _calculate_streaks(self, config_id: str):
        """Calculate consecutive win/loss streaks"""
        trades_list = self.trades[config_id]
        if not trades_list:
            return
        
        stats = self.stats[config_id]
        
        current_streak = 0
        current_type = "NONE"
        max_wins = 0
        max_losses = 0
        temp_wins = 0
        temp_losses = 0
        
        for trade in trades_list:
            if trade.status == "WIN":
                if current_type == "WIN":
                    temp_wins += 1
                else:
                    max_losses = max(max_losses, temp_losses)
                    temp_losses = 0
                    temp_wins = 1
                    current_type = "WIN"
            elif trade.status == "LOSS":
                if current_type == "LOSS":
                    temp_losses += 1
                else:
                    max_wins = max(max_wins, temp_wins)
                    temp_wins = 0
                    temp_losses = 1
                    current_type = "LOSS"
        
        # Update final streaks
        max_wins = max(max_wins, temp_wins)
        max_losses = max(max_losses, temp_losses)
        
        stats.max_consecutive_wins = max_wins
        stats.max_consecutive_losses = max_losses
        stats.current_streak = temp_wins if current_type == "WIN" else temp_losses
        stats.current_streak_type = current_type
    
    def _calculate_drawdown_runup(self, config_id: str):
        """Calculate maximum drawdown and runup"""
        trades_list = self.trades[config_id]
        if not trades_list:
            return
        
        stats = self.stats[config_id]
        
        # Calculate running P&L
        running_pnl = 0
        peak_pnl = 0
        trough_pnl = 0
        max_drawdown = 0
        max_runup = 0
        
        for trade in trades_list:
            running_pnl += trade.pnl
            
            # Update peak
            if running_pnl > peak_pnl:
                peak_pnl = running_pnl
            
            # Update trough
            if running_pnl < trough_pnl:
                trough_pnl = running_pnl
            
            # Calculate drawdown from peak
            current_drawdown = peak_pnl - running_pnl
            max_drawdown = max(max_drawdown, current_drawdown)
            
            # Calculate runup from trough
            current_runup = running_pnl - trough_pnl
            max_runup = max(max_runup, current_runup)
        
        stats.max_drawdown = max_drawdown
        stats.max_runup = max_runup
    
    def get_performance_report(self, config_id: Optional[str] = None) -> Dict:
        """Generate comprehensive performance report"""
        if config_id:
            return self._generate_single_config_report(config_id)
        else:
            return self._generate_all_configs_report()
    
    def _generate_single_config_report(self, config_id: str) -> Dict:
        """Generate detailed report for a single configuration"""
        if config_id not in self.stats:
            return {"error": f"Configuration {config_id} not found"}
        
        config = self.configurations[config_id]
        stats = self.stats[config_id]
        
        return {
            "config_id": config_id,
            "name": config["name"],
            "description": config["description"],
            "settings": config["settings"],
            
            # All Trades Section
            "all_trades": {
                "gross_pnl": stats.gross_pnl,
                "num_trades": stats.total_trades,
                "num_contracts": stats.total_contracts,
                "avg_trade_time": f"{stats.avg_trade_time_minutes:.1f} minutes",
                "longest_trade_time": f"{stats.longest_trade_time_minutes} minutes",
                "percent_profitable_trades": f"{stats.percent_profitable:.2f}%",
                "expectancy": f"${stats.expectancy:.2f}",
                "trade_fees_commission": f"${stats.trade_fees_commission:.2f}",
                "total_pnl": f"${stats.total_pnl:.2f}"
            },
            
            # Profit Trades Section
            "profit_trades": {
                "total_profit": f"${stats.total_profit:.2f}",
                "num_winning_trades": stats.winning_trades,
                "num_winning_contracts": stats.winning_contracts,
                "largest_winning_trade": f"${stats.largest_winning_trade:.2f}",
                "avg_winning_trade": f"${stats.avg_winning_trade:.2f}",
                "std_dev_winning_trade": f"${stats.std_dev_winning_trade:.2f}",
                "avg_winning_trade_time": f"{stats.avg_winning_trade_time_minutes:.1f} minutes",
                "longest_winning_trade_time": f"{stats.longest_winning_trade_time_minutes} minutes",
                "max_runup": f"${stats.max_runup:.2f}"
            },
            
            # Losing Trades Section
            "losing_trades": {
                "total_loss": f"${stats.total_loss:.2f}",
                "num_losing_trades": stats.losing_trades,
                "num_losing_contracts": stats.losing_contracts,
                "largest_losing_trade": f"${stats.largest_losing_trade:.2f}",
                "avg_losing_trade": f"${stats.avg_losing_trade:.2f}",
                "std_dev_losing_trade": f"${stats.std_dev_losing_trade:.2f}",
                "avg_losing_trade_time": f"{stats.avg_losing_trade_time_minutes:.1f} minutes",
                "longest_losing_trade_time": f"{stats.longest_losing_trade_time_minutes} minutes",
                "max_drawdown": f"${stats.max_drawdown:.2f}"
            },
            
            # Additional Performance Metrics
            "performance_metrics": {
                "win_rate": f"{stats.win_rate:.2f}%",
                "profit_factor": f"{stats.profit_factor:.2f}",
                "max_consecutive_wins": stats.max_consecutive_wins,
                "max_consecutive_losses": stats.max_consecutive_losses,
                "current_streak": stats.current_streak,
                "current_streak_type": stats.current_streak_type
            },
            
            "last_updated": datetime.now().isoformat()
        }
    
    def _generate_all_configs_report(self) -> Dict:
        """Generate summary report for all configurations"""
        reports = {}
        for config_id in self.configurations:
            reports[config_id] = self._generate_single_config_report(config_id)
        
        # Generate leaderboard
        leaderboard = self._generate_leaderboard()
        
        return {
            "summary": {
                "total_configurations": len(self.configurations),
                "total_active_trades": len(self.active_trades),
                "generated_at": datetime.now().isoformat()
            },
            "leaderboard": leaderboard,
            "detailed_reports": reports
        }
    
    def _generate_leaderboard(self) -> List[Dict]:
        """Generate performance leaderboard"""
        leaderboard = []
        
        for config_id, stats in self.stats.items():
            config = self.configurations.get(config_id, {})
            
            leaderboard.append({
                "rank": 0,  # Will be set after sorting
                "config_id": config_id,
                "name": config.get("name", "Unknown"),
                "total_pnl": stats.total_pnl,
                "total_trades": stats.total_trades,
                "win_rate": stats.win_rate,
                "profit_factor": stats.profit_factor,
                "expectancy": stats.expectancy,
                "max_drawdown": stats.max_drawdown
            })
        
        # Sort by total P&L descending
        leaderboard.sort(key=lambda x: x["total_pnl"], reverse=True)
        
        # Set ranks
        for i, entry in enumerate(leaderboard, 1):
            entry["rank"] = i
        
        return leaderboard
    
    def export_to_csv(self, config_id: Optional[str] = None) -> str:
        """Export trade data to CSV"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if config_id:
            filename = f"{self.data_dir}/trades_{config_id}_{timestamp}.csv"
            trades_to_export = self.trades.get(config_id, [])
        else:
            filename = f"{self.data_dir}/trades_all_{timestamp}.csv"
            trades_to_export = []
            for trade_list in self.trades.values():
                trades_to_export.extend(trade_list)
        
        # Convert to DataFrame and save
        if trades_to_export:
            df = pd.DataFrame([asdict(trade) for trade in trades_to_export])
            df.to_csv(filename, index=False)
            logging.info(f"Exported {len(trades_to_export)} trades to {filename}")
        else:
            logging.warning("No trades to export")
        
        return filename
    
    def _save_data(self):
        """Save all data to disk"""
        # Save configurations
        with open(f"{self.data_dir}/configurations.json", "w") as f:
            json.dump(self.configurations, f, indent=2, default=str)
        
        # Save trades
        trades_data = {}
        for config_id, trade_list in self.trades.items():
            trades_data[config_id] = [asdict(trade) for trade in trade_list]
        
        with open(f"{self.data_dir}/trades.json", "w") as f:
            json.dump(trades_data, f, indent=2, default=str)
        
        # Save active trades
        active_trades_data = {k: asdict(v) for k, v in self.active_trades.items()}
        with open(f"{self.data_dir}/active_trades.json", "w") as f:
            json.dump(active_trades_data, f, indent=2, default=str)
        
        # Save stats
        stats_data = {k: asdict(v) for k, v in self.stats.items()}
        with open(f"{self.data_dir}/stats.json", "w") as f:
            json.dump(stats_data, f, indent=2, default=str)
    
    def _load_data(self):
        """Load existing data from disk"""
        try:
            # Load configurations
            config_file = f"{self.data_dir}/configurations.json"
            if os.path.exists(config_file):
                with open(config_file, "r") as f:
                    self.configurations = json.load(f)
            
            # Load trades
            trades_file = f"{self.data_dir}/trades.json"
            if os.path.exists(trades_file):
                with open(trades_file, "r") as f:
                    trades_data = json.load(f)
                    for config_id, trade_list in trades_data.items():
                        self.trades[config_id] = [
                            TradeRecord(**{k: datetime.fromisoformat(v) if k.endswith('_time') and v else v 
                                         for k, v in trade.items()})
                            for trade in trade_list
                        ]
            
            # Load active trades
            active_file = f"{self.data_dir}/active_trades.json"
            if os.path.exists(active_file):
                with open(active_file, "r") as f:
                    active_data = json.load(f)
                    for config_id, trade_data in active_data.items():
                        # Convert datetime strings back to datetime objects
                        for key in ['entry_time', 'exit_time']:
                            if trade_data.get(key):
                                trade_data[key] = datetime.fromisoformat(trade_data[key])
                        self.active_trades[config_id] = TradeRecord(**trade_data)
            
            # Load stats
            stats_file = f"{self.data_dir}/stats.json"
            if os.path.exists(stats_file):
                with open(stats_file, "r") as f:
                    stats_data = json.load(f)
                    for config_id, stat_dict in stats_data.items():
                        self.stats[config_id] = ConfigurationStats(**stat_dict)
            
            # Initialize missing stats
            for config_id in self.configurations:
                if config_id not in self.stats:
                    config = self.configurations[config_id]
                    self.stats[config_id] = ConfigurationStats(
                        config_id=config_id,
                        name=config.get("name", "Unknown")
                    )
        
        except Exception as e:
            logging.error(f"Error loading data: {e}")

# Create global instance
test_manager = EnhancedForwardTestManager()
