"""
TSLA Strategy A Test Bot - Paper Trading on Alpaca
===================================================

This is a live trading bot that implements the STANDARD Strategy A 
(FCR with FVG confirmation) from strategy.py for TSLA on Alpaca's 
paper trading account.

Strategy Name: TSLA Strategy A Test Bot
Ticker: TSLA only
Risk per Trade: $500
Capital: $5000
Account: Alpaca Paper Trading

Uses Standard Strategy A Logic from strategy.py:
- 5-min first candle as anchor
- 1-min FVG detection timeframe
- Breakout continuation pattern (price breaks FC high/low before FVG)
- R:R ratio of 5.5:1
- NO additional filters (uses exact same logic as backtest)
"""

import os
import sys
import time
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

import pandas as pd
import pytz

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from config import ALPACA_API_KEY, ALPACA_API_SECRET, Timezone

# Import the STANDARD Strategy A logic from strategy.py
from strategy import (
    get_strategy_config,
    detect_fvgs,
    check_retest_and_engulf,
    filter_by_first_candle_break
)

# ═══════════════════════════════════════════════════════════════════════════════
# BOT CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

BOT_CONFIG = {
    "name": "TSLA Strategy A Test Bot",
    "ticker": "TSLA",
    "risk_per_trade": 500,      # $500 risk per trade
    "capital": 5000,            # $5000 starting capital
    "strategy_type": "A",       # Use Strategy A from strategy.py
    "custom_rr": 5.5,           # R:R ratio (same as Strategy A default)
    "max_trades_per_day": 1,    # Max trades per day
    "paper_trading": True,      # Use paper trading account
}

# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING SETUP
# ═══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('tsla_bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Timezone
ET = pytz.timezone(Timezone)


class BotState(Enum):
    IDLE = "idle"
    WAITING_MARKET_OPEN = "waiting_market_open"
    WAITING_FIRST_CANDLE = "waiting_first_candle"
    SCANNING_FOR_SIGNAL = "scanning_for_signal"
    IN_POSITION = "in_position"
    STOPPED = "stopped"


@dataclass
class FirstCandle:
    """Stores first candle data for the trading day."""
    high: float
    low: float
    open: float
    close: float
    time: datetime
    close_time: datetime


@dataclass
class ActiveTrade:
    """Tracks an active trade."""
    entry_price: float
    stop_price: float
    target_price: float
    direction: str  # "LONG" or "SHORT"
    entry_time: datetime
    quantity: int
    risk_amount: float
    order_id: Optional[str] = None


class TslaStrategyABot:
    """
    TSLA Strategy A Live Trading Bot for Alpaca Paper Trading.
    
    Implements the STANDARD FCR Strategy A from strategy.py:
    1. Captures the first 5-min candle at market open (9:30 AM ET)
    2. Scans for Fair Value Gaps on 1-min chart using detect_fvgs()
    3. Checks for retest and engulf using check_retest_and_engulf()
    4. Validates first candle break using filter_by_first_candle_break()
    5. Enters trades with the configured R:R ratio
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or BOT_CONFIG
        self.ticker = self.config["ticker"]
        
        # Get Strategy A configuration from strategy.py
        self.strategy_config = get_strategy_config(
            self.config["strategy_type"], 
            self.config.get("custom_rr")
        )
        self.anchor_min = self.strategy_config["anchor_min"]  # 5 min
        self.confirm_min = self.strategy_config["confirm_min"]  # 1 min
        self.rr = self.strategy_config["rr"]  # 5.5
        
        # Initialize Alpaca clients
        self.paper_trading = self.config.get("paper_trading", True)
        
        # Trading client (for orders)
        self.trading_client = TradingClient(
            ALPACA_API_KEY, 
            ALPACA_API_SECRET, 
            paper=self.paper_trading
        )
        
        # Data client (for market data)
        self.data_client = StockHistoricalDataClient(
            ALPACA_API_KEY, 
            ALPACA_API_SECRET
        )
        
        # Bot state
        self.state = BotState.IDLE
        self.first_candle: Optional[FirstCandle] = None
        self.active_trade: Optional[ActiveTrade] = None
        self.trades_today = 0
        self.today_date: Optional[datetime.date] = None
        
        # Control flags
        self.running = False
        
        # Statistics
        self.stats = {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
        }
        
        logger.info(f"🤖 {self.config['name']} initialized")
        logger.info(f"   Ticker: {self.ticker}")
        logger.info(f"   Strategy: {self.strategy_config['label']}")
        logger.info(f"   Risk per trade: ${self.config['risk_per_trade']}")
        logger.info(f"   R:R Ratio: {self.rr}:1")
        logger.info(f"   Paper Trading: {self.paper_trading}")
    
    def get_account_info(self) -> Dict[str, Any]:
        """Get current account information."""
        try:
            account = self.trading_client.get_account()
            return {
                "buying_power": float(account.buying_power),
                "equity": float(account.equity),
                "cash": float(account.cash),
                "portfolio_value": float(account.portfolio_value),
                "status": account.status,
            }
        except Exception as e:
            logger.error(f"Error getting account info: {e}")
            return {}
    
    def is_market_open(self) -> bool:
        """Check if the market is currently open."""
        try:
            clock = self.trading_client.get_clock()
            return clock.is_open
        except Exception as e:
            logger.error(f"Error checking market status: {e}")
            return False
    
    def get_time_until_market_open(self) -> Optional[timedelta]:
        """Get time remaining until market opens."""
        try:
            clock = self.trading_client.get_clock()
            if clock.is_open:
                return timedelta(0)
            return clock.next_open - clock.timestamp
        except Exception as e:
            logger.error(f"Error getting market open time: {e}")
            return None
    
    def get_current_price(self) -> Optional[float]:
        """Get the current price of TSLA."""
        try:
            request = StockLatestQuoteRequest(symbol_or_symbols=self.ticker)
            quote = self.data_client.get_stock_latest_quote(request)
            if self.ticker in quote:
                return float(quote[self.ticker].ask_price)
            return None
        except Exception as e:
            logger.error(f"Error getting current price: {e}")
            return None
    
    def fetch_recent_bars(self, minutes: int = 60) -> pd.DataFrame:
        """Fetch recent 1-minute bars for analysis."""
        try:
            end_time = datetime.now(ET)
            start_time = end_time - timedelta(minutes=minutes + 5)
            
            request = StockBarsRequest(
                symbol_or_symbols=self.ticker,
                timeframe=TimeFrame.Minute,
                start=start_time,
                end=end_time
            )
            
            bars = self.data_client.get_stock_bars(request)
            
            if self.ticker not in bars.data or len(bars.data[self.ticker]) == 0:
                return pd.DataFrame()
            
            records = []
            for bar in bars.data[self.ticker]:
                records.append({
                    "timestamp": bar.timestamp,
                    "Open": float(bar.open),
                    "High": float(bar.high),
                    "Low": float(bar.low),
                    "Close": float(bar.close),
                    "Volume": int(bar.volume)
                })
            
            df = pd.DataFrame(records)
            df = df.set_index("timestamp")
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            df.index = df.index.tz_convert(ET)
            
            return df
            
        except Exception as e:
            logger.error(f"Error fetching recent bars: {e}")
            return pd.DataFrame()
    
    def capture_first_candle(self) -> Optional[FirstCandle]:
        """
        Capture the first 5-minute candle at market open (9:30-9:35 AM ET).
        Uses the same logic as backtester - no filtering, just capture the candle.
        """
        try:
            now = datetime.now(ET)
            
            # We need to get the 9:30 candle after it closes at 9:35
            market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
            candle_close = market_open + timedelta(minutes=self.anchor_min)
            
            # If we're before 9:35, wait
            if now < candle_close:
                logger.info(f"Waiting for first candle to close at 9:35 AM ET...")
                return None
            
            # Fetch the 9:30 candle
            start_time = market_open - timedelta(minutes=1)
            end_time = candle_close + timedelta(minutes=1)
            
            request = StockBarsRequest(
                symbol_or_symbols=self.ticker,
                timeframe=TimeFrame(self.anchor_min, TimeFrameUnit.Minute),
                start=start_time,
                end=end_time
            )
            
            bars = self.data_client.get_stock_bars(request)
            
            if self.ticker not in bars.data or len(bars.data[self.ticker]) == 0:
                logger.warning("No data for first candle")
                return None
            
            # Get the 9:30 bar
            bar = bars.data[self.ticker][0]
            
            fc_high = float(bar.high)
            fc_low = float(bar.low)
            fc_open = float(bar.open)
            fc_close = float(bar.close)
            
            # Convert bar timestamp to ET
            bar_time = bar.timestamp
            if bar_time.tzinfo is None:
                bar_time = pytz.utc.localize(bar_time)
            bar_time = bar_time.astimezone(ET)
            
            first_candle = FirstCandle(
                high=fc_high,
                low=fc_low,
                open=fc_open,
                close=fc_close,
                time=bar_time,
                close_time=bar_time + timedelta(minutes=self.anchor_min)
            )
            
            logger.info(f"✅ First Candle captured: High=${fc_high:.2f}, Low=${fc_low:.2f}")
            
            return first_candle
            
        except Exception as e:
            logger.error(f"Error capturing first candle: {e}")
            return None
    
    def scan_for_signals(self) -> Optional[Dict[str, Any]]:
        """
        Scan for trading signals using the STANDARD Strategy A logic from strategy.py.
        
        Uses:
        - detect_fvgs() from strategy.py
        - check_retest_and_engulf() from strategy.py
        - filter_by_first_candle_break() from strategy.py
        """
        if not self.first_candle:
            return None
        
        if self.trades_today >= self.config["max_trades_per_day"]:
            return None
        
        # Fetch recent 1-min bars (confirmation timeframe)
        df = self.fetch_recent_bars(minutes=120)
        if df.empty:
            return None
        
        # Use the STANDARD detect_fvgs from strategy.py
        fvgs = detect_fvgs(df)
        
        for fvg in fvgs:
            # Use the STANDARD check_retest_and_engulf from strategy.py
            signal = check_retest_and_engulf(df, fvg)
            
            if signal:
                # Use the STANDARD filter_by_first_candle_break from strategy.py
                # This validates Strategy A breakout continuation pattern
                if filter_by_first_candle_break(
                    df,
                    self.first_candle.high,
                    self.first_candle.low,
                    signal,
                    strategy_type='A',
                    first_candle_close_time=pd.Timestamp(self.first_candle.close_time)
                ):
                    logger.info(f"🎯 Signal detected: {signal['direction']} at ${signal['entry_price']:.2f}")
                    return signal
        
        return None
    
    def calculate_position_size(self, entry_price: float, stop_price: float) -> int:
        """Calculate position size based on risk per trade."""
        risk_per_share = abs(entry_price - stop_price)
        
        if risk_per_share <= 0:
            return 0
        
        # Get account info to check buying power
        account = self.get_account_info()
        buying_power = account.get("buying_power", 0)
        
        # Calculate shares based on risk
        shares = int(self.config["risk_per_trade"] / risk_per_share)
        
        # Ensure we don't exceed buying power
        max_shares_by_capital = int(buying_power / entry_price)
        shares = min(shares, max_shares_by_capital)
        
        return max(shares, 0)
    
    def place_market_order(self, side: str, quantity: int) -> Optional[str]:
        """Place a market order on Alpaca."""
        try:
            order_side = OrderSide.BUY if side == "BUY" else OrderSide.SELL
            
            order_request = MarketOrderRequest(
                symbol=self.ticker,
                qty=quantity,
                side=order_side,
                time_in_force=TimeInForce.DAY
            )
            
            order = self.trading_client.submit_order(order_request)
            logger.info(f"📤 Order placed: {side} {quantity} {self.ticker} - Order ID: {order.id}")
            
            return order.id
            
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return None
    
    def close_position(self) -> bool:
        """Close the current position."""
        try:
            if not self.active_trade:
                return True
            
            # Determine close side (opposite of entry)
            close_side = "SELL" if self.active_trade.direction == "LONG" else "BUY"
            
            order_id = self.place_market_order(close_side, self.active_trade.quantity)
            
            if order_id:
                logger.info(f"📥 Position closed - Order ID: {order_id}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            return False
    
    def manage_position(self) -> None:
        """
        Manage the active position - check stops and targets.
        Standard behavior: no trailing stop, just fixed stop and target.
        """
        if not self.active_trade:
            return
        
        current_price = self.get_current_price()
        if not current_price:
            return
        
        entry = self.active_trade.entry_price
        stop = self.active_trade.stop_price
        target = self.active_trade.target_price
        direction = self.active_trade.direction
        
        # Check stop hit
        stop_hit = False
        if direction == "LONG" and current_price <= stop:
            stop_hit = True
        elif direction == "SHORT" and current_price >= stop:
            stop_hit = True
        
        if stop_hit:
            if self.close_position():
                pnl = -self.active_trade.risk_amount
                self.stats["losses"] += 1
                self.stats["total_trades"] += 1
                self.stats["total_pnl"] += pnl
                logger.info(f"🔴 Trade stopped out - PnL: ${pnl:.2f}")
                self.active_trade = None
                self.state = BotState.SCANNING_FOR_SIGNAL
            return
        
        # Check target hit
        target_hit = False
        if direction == "LONG" and current_price >= target:
            target_hit = True
        elif direction == "SHORT" and current_price <= target:
            target_hit = True
        
        if target_hit:
            if self.close_position():
                pnl = self.active_trade.risk_amount * self.rr
                self.stats["wins"] += 1
                self.stats["total_trades"] += 1
                self.stats["total_pnl"] += pnl
                logger.info(f"🟢 Target hit! PnL: ${pnl:.2f}")
                self.active_trade = None
                self.state = BotState.SCANNING_FOR_SIGNAL
    
    def execute_signal(self, signal: Dict[str, Any]) -> bool:
        """Execute a trading signal."""
        try:
            entry_price = signal['entry_price']
            stop_price = signal['stop_price']
            direction = signal['direction']
            
            risk_dist = abs(entry_price - stop_price)
            target_price = (entry_price + (risk_dist * self.rr) 
                           if direction == "LONG" 
                           else entry_price - (risk_dist * self.rr))
            
            # Calculate position size
            quantity = self.calculate_position_size(entry_price, stop_price)
            
            if quantity <= 0:
                logger.warning("Position size too small - skipping trade")
                return False
            
            # Place order
            order_side = "BUY" if direction == "LONG" else "SELL"
            order_id = self.place_market_order(order_side, quantity)
            
            if not order_id:
                return False
            
            # Create active trade record
            self.active_trade = ActiveTrade(
                entry_price=entry_price,
                stop_price=stop_price,
                target_price=target_price,
                direction=direction,
                entry_time=datetime.now(ET),
                quantity=quantity,
                risk_amount=self.config["risk_per_trade"],
                order_id=order_id
            )
            
            self.trades_today += 1
            self.state = BotState.IN_POSITION
            
            logger.info(f"📊 Trade opened: {direction} {quantity} shares at ${entry_price:.2f}")
            logger.info(f"   Stop: ${stop_price:.2f}, Target: ${target_price:.2f}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error executing signal: {e}")
            return False
    
    def reset_daily_state(self) -> None:
        """Reset state for a new trading day."""
        self.first_candle = None
        self.trades_today = 0
        self.today_date = datetime.now(ET).date()
        logger.info("🔄 Daily state reset")
    
    def run_loop(self) -> None:
        """Main trading loop."""
        self.running = True
        logger.info("🚀 Bot started - entering main loop")
        logger.info(f"   Using Strategy: {self.strategy_config['label']}")
        
        while self.running:
            try:
                now = datetime.now(ET)
                
                # Check for new day
                if self.today_date != now.date():
                    self.reset_daily_state()
                
                # State machine
                if self.state == BotState.STOPPED:
                    break
                
                elif self.state == BotState.IDLE or self.state == BotState.WAITING_MARKET_OPEN:
                    if self.is_market_open():
                        self.state = BotState.WAITING_FIRST_CANDLE
                        logger.info("📈 Market is open - waiting for first candle")
                    else:
                        time_until = self.get_time_until_market_open()
                        if time_until and time_until.total_seconds() > 0:
                            mins = int(time_until.total_seconds() / 60)
                            if mins % 30 == 0:  # Log every 30 minutes
                                logger.info(f"⏳ Waiting for market open ({mins} minutes)")
                        self.state = BotState.WAITING_MARKET_OPEN
                
                elif self.state == BotState.WAITING_FIRST_CANDLE:
                    # Wait until 9:35 to capture first candle
                    if now.hour == 9 and now.minute >= 35:
                        self.first_candle = self.capture_first_candle()
                        if self.first_candle:
                            self.state = BotState.SCANNING_FOR_SIGNAL
                            logger.info("🔍 Scanning for Strategy A signals...")
                    elif now.hour > 9:
                        # After 9:35, try to capture
                        self.first_candle = self.capture_first_candle()
                        if self.first_candle:
                            self.state = BotState.SCANNING_FOR_SIGNAL
                            logger.info("🔍 Scanning for Strategy A signals...")
                
                elif self.state == BotState.SCANNING_FOR_SIGNAL:
                    # Check if market still open
                    if not self.is_market_open():
                        logger.info("📴 Market closed - waiting for next session")
                        self.state = BotState.WAITING_MARKET_OPEN
                    else:
                        signal = self.scan_for_signals()
                        if signal:
                            if self.execute_signal(signal):
                                self.state = BotState.IN_POSITION
                
                elif self.state == BotState.IN_POSITION:
                    self.manage_position()
                    # Check if market closed while in position
                    if not self.is_market_open() and self.active_trade:
                        logger.info("📴 Market closed with open position - will manage on next open")
                
                # Sleep between iterations
                time.sleep(5)  # Check every 5 seconds
                
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(10)
        
        logger.info("🛑 Bot stopped")
    
    def start(self) -> None:
        """Start the bot in a background thread."""
        self.state = BotState.IDLE
        self.reset_daily_state()
        
        thread = threading.Thread(target=self.run_loop, daemon=True)
        thread.start()
        logger.info("🤖 Bot thread started")
    
    def stop(self) -> None:
        """Stop the bot."""
        logger.info("🛑 Stopping bot...")
        self.running = False
        self.state = BotState.STOPPED
    
    def get_status(self) -> Dict[str, Any]:
        """Get current bot status."""
        return {
            "name": self.config["name"],
            "state": self.state.value,
            "running": self.running,
            "ticker": self.ticker,
            "paper_trading": self.paper_trading,
            "trades_today": self.trades_today,
            "strategy": self.strategy_config["label"],
            "first_candle": {
                "high": self.first_candle.high if self.first_candle else None,
                "low": self.first_candle.low if self.first_candle else None,
                "valid": True if self.first_candle else None,
            },
            "active_trade": {
                "direction": self.active_trade.direction if self.active_trade else None,
                "entry_price": self.active_trade.entry_price if self.active_trade else None,
                "stop_price": self.active_trade.stop_price if self.active_trade else None,
                "target_price": self.active_trade.target_price if self.active_trade else None,
            },
            "stats": self.stats,
            "config": {
                "risk_per_trade": self.config["risk_per_trade"],
                "capital": self.config["capital"],
                "rr": self.rr,
            }
        }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

# Global bot instance for use by Flask app
_bot_instance: Optional[TslaStrategyABot] = None


def get_bot() -> TslaStrategyABot:
    """Get or create the bot instance."""
    global _bot_instance
    if _bot_instance is None:
        _bot_instance = TslaStrategyABot()
    return _bot_instance


def start_bot() -> Dict[str, Any]:
    """Start the trading bot."""
    bot = get_bot()
    if not bot.running:
        bot.start()
        return {"status": "started", "message": "Bot started successfully"}
    return {"status": "already_running", "message": "Bot is already running"}


def stop_bot() -> Dict[str, Any]:
    """Stop the trading bot."""
    bot = get_bot()
    if bot.running:
        bot.stop()
        return {"status": "stopped", "message": "Bot stopped successfully"}
    return {"status": "not_running", "message": "Bot is not running"}


def get_bot_status() -> Dict[str, Any]:
    """Get the current bot status."""
    bot = get_bot()
    return bot.get_status()


if __name__ == "__main__":
    print("=" * 70)
    print("  TSLA Strategy A Test Bot - Paper Trading")
    print("  Using STANDARD Strategy A from strategy.py")
    print("=" * 70)
    print()
    
    bot = TslaStrategyABot()
    
    # Show strategy info
    print(f"Strategy: {bot.strategy_config['label']}")
    print(f"Anchor Timeframe: {bot.anchor_min} min")
    print(f"Confirm Timeframe: {bot.confirm_min} min")
    print(f"R:R Ratio: {bot.rr}:1")
    print()
    
    # Show account info
    account = bot.get_account_info()
    print(f"Account Status: {account.get('status', 'N/A')}")
    print(f"Equity: ${account.get('equity', 0):,.2f}")
    print(f"Buying Power: ${account.get('buying_power', 0):,.2f}")
    print()
    
    # Check market status
    if bot.is_market_open():
        print("✅ Market is OPEN")
    else:
        time_until = bot.get_time_until_market_open()
        if time_until:
            hours = int(time_until.total_seconds() / 3600)
            mins = int((time_until.total_seconds() % 3600) / 60)
            print(f"⏳ Market opens in {hours}h {mins}m")
    
    print()
    print("Press Ctrl+C to stop the bot")
    print()
    
    try:
        bot.start()
        # Keep main thread alive
        while bot.running:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        bot.stop()
        print("Bot stopped.")
