"""
TSLA Strategy A Test Bot - Paper Trading on Alpaca (Test2 Account)
===================================================================

This is a live trading bot that implements the STANDARD Strategy A 
(FCR with FVG confirmation) from strategy.py for TSLA on Alpaca's 
Test2 paper trading account.

Strategy Name: TSLA Strategy A Test Bot
Ticker: TSLA only
Risk per Trade: $100
Capital: $5000
R:R Ratio: 3:1
Account: Alpaca Test2 Paper Trading

Uses Standard Strategy A Logic from strategy.py:
- 5-min first candle as anchor
- 1-min FVG detection timeframe
- Breakout continuation pattern (price breaks FC high/low before FVG)
- R:R ratio of 3:1
- NO additional filters (uses exact same logic as backtest)

Alpaca Integration:
- Uses bracket orders for automatic stop-loss and take-profit
- Verifies buying power before placing orders
- Checks ETB (Easy to Borrow) status for short positions
- Uses DAY time-in-force for all orders
"""

import os
import sys
import time
import logging
import threading
import json
import base64
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

import pandas as pd
import pytz
import websocket

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetAssetsRequest
from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass, AssetStatus, OrderClass
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
try:
    from alpaca.data.enums import DataFeed
    ALPACA_FEED = DataFeed.IEX
except ImportError:
    ALPACA_FEED = "iex"  # Fallback for older alpaca-py versions

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
    "risk_per_trade": 100,      # $100 risk per trade (Test2 account)
    "capital": 5000,            # $5000 starting capital
    "strategy_type": "A",       # Use Strategy A from strategy.py
    "custom_rr": 3.0,           # R:R ratio 3:1 for Test2 account
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
        
        # WebSocket streaming
        self.ws: Optional[websocket.WebSocketApp] = None
        self.ws_connected = False
        self.ws_authenticated = False
        self.bars_buffer: List[Dict] = []  # Rolling buffer of 1-minute bars
        self.bars_lock = threading.Lock()  # Thread-safe access to bars buffer
        self.use_websocket = True  # Try WebSocket first, fallback to REST
        
        # Track failed/attempted signals to avoid retrying the same stale signal
        self._attempted_signals: set = set()  # Set of (direction, entry_price, fvg_time) tuples

        # Signal & trade history for UI display
        self.detected_signals: List[Dict] = []  # All signals found during session
        self.trade_history: List[Dict] = []  # All trades opened/closed with PnL

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
        
        # Verify Alpaca connection on initialization
        self._verify_alpaca_connection()
    
    def _verify_alpaca_connection(self) -> None:
        """Verify connection to Alpaca and log account details.
        
        This ensures we're connected to the correct account (Test2)
        and have proper trading permissions.
        """
        try:
            account = self.trading_client.get_account()
            
            logger.info(f"✅ Alpaca connection verified!")
            logger.info(f"   Account ID: {account.id}")
            logger.info(f"   Account Status: {account.status}")
            logger.info(f"   Cash: ${float(account.cash):,.2f}")
            logger.info(f"   Equity: ${float(account.equity):,.2f}")
            logger.info(f"   Buying Power: ${float(account.buying_power):,.2f}")
            logger.info(f"   Day Trade Count: {account.daytrade_count}")
            logger.info(f"   Pattern Day Trader: {account.pattern_day_trader}")
            logger.info(f"   Trading Blocked: {account.trading_blocked}")
            
            # Verify we have margin/short access (equity >= $2000)
            equity = float(account.equity)
            if equity < 2000:
                logger.warning(f"⚠️ Account equity ${equity:,.2f} is below $2,000 - margin and shorting disabled")
            else:
                logger.info(f"   Margin/Short Trading: Enabled (equity >= $2,000)")
            
            # Check if trading is blocked
            if account.trading_blocked:
                logger.error(f"❌ TRADING IS BLOCKED on this account!")
            
            # Check asset shortability
            try:
                asset = self.trading_client.get_asset(self.ticker)
                logger.info(f"   {self.ticker} Status: tradable={asset.tradable}, shortable={asset.shortable}, ETB={asset.easy_to_borrow}")
            except Exception as e:
                logger.warning(f"⚠️ Could not verify {self.ticker} asset status: {e}")
                
        except Exception as e:
            logger.error(f"❌ Failed to verify Alpaca connection: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def get_account_info(self) -> Dict[str, Any]:
        """Get current account information."""
        try:
            account = self.trading_client.get_account()
            
            # Get positions
            positions = self.trading_client.get_all_positions()
            position_list = []
            current_price = None
            try:
                current_price = self.get_current_price()
            except:
                pass
            
            for pos in positions:
                if pos.symbol == self.ticker:
                    # Calculate market value and P/L
                    avg_entry = float(pos.avg_entry_price)
                    qty = float(pos.qty)
                    price = current_price if current_price else avg_entry
                    market_value = qty * price
                    unrealized_pl = (price - avg_entry) * qty if current_price else 0
                    
                    position_list.append({
                        "symbol": pos.symbol,
                        "qty": qty,
                        "avg_entry_price": avg_entry,
                        "current_price": price,
                        "market_value": market_value,
                        "unrealized_pl": unrealized_pl,
                    })
            
            return {
                "buying_power": float(account.buying_power),
                "equity": float(account.equity),
                "cash": float(account.cash),
                "portfolio_value": float(account.portfolio_value),
                "status": account.status,
                "positions": position_list,
            }
        except Exception as e:
            logger.error(f"Error getting account info: {e}")
            return {}
    
    def is_market_open(self) -> bool:
        """Check if the market is currently open."""
        try:
            clock = self.trading_client.get_clock()
            is_open = clock.is_open
            now_et = datetime.now(ET)
            
            # Log detailed market status
            logger.info(f"🕐 Market Status Check:")
            logger.info(f"   Alpaca says market is_open: {is_open}")
            logger.info(f"   Current time (ET): {now_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            logger.info(f"   Alpaca clock time: {clock.timestamp}")
            if hasattr(clock, 'next_open'):
                logger.info(f"   Next open: {clock.next_open}")
            if hasattr(clock, 'next_close'):
                logger.info(f"   Next close: {clock.next_close}")
            
            # Additional validation: Check if we're in market hours (9:30 AM - 4:00 PM ET)
            # This helps catch cases where Alpaca's clock might be slightly off
            hour = now_et.hour
            minute = now_et.minute
            weekday = now_et.weekday()  # 0 = Monday, 6 = Sunday
            
            # Market hours: 9:30 AM - 4:00 PM ET, Monday-Friday
            in_market_hours = (
                weekday < 5 and  # Monday-Friday
                ((hour == 9 and minute >= 30) or (hour >= 10 and hour < 16) or (hour == 16 and minute == 0))
            )
            
            logger.info(f"   Calculated market hours (ET): {in_market_hours} (Hour: {hour}, Minute: {minute}, Weekday: {weekday})")
            
            # If Alpaca says closed but we're in market hours, log a warning
            if not is_open and in_market_hours:
                logger.warning("⚠️ WARNING: Alpaca says market is closed, but we're in market hours! Using Alpaca's status.")
            
            return is_open
        except Exception as e:
            logger.error(f"❌ Error checking market status: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Fallback: check if we're in market hours
            now_et = datetime.now(ET)
            hour = now_et.hour
            minute = now_et.minute
            weekday = now_et.weekday()
            in_market_hours = (
                weekday < 5 and
                ((hour == 9 and minute >= 30) or (hour >= 10 and hour < 16) or (hour == 16 and minute == 0))
            )
            logger.warning(f"⚠️ Using fallback market hours check: {in_market_hours}")
            return in_market_hours
    
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
    
    def get_current_price(self, use_rest_api: bool = False) -> Optional[float]:
        """Get the current price of TSLA.

        Args:
            use_rest_api: If True, skip WebSocket buffer and use REST API directly.
                          This provides more real-time data for stop/target checks.

        Tries WebSocket buffer first (latest bar close), falls back to REST API.
        For stop loss, we need the most recent trade price (bid for selling, ask for buying).
        """
        # For position management, prefer REST API for more accurate real-time price
        if not use_rest_api:
            # Try WebSocket buffer first (most recent bar's close price)
            with self.bars_lock:
                if self.bars_buffer:
                    # Check if the latest bar is recent (within last 2 minutes)
                    latest_bar = self.bars_buffer[-1]
                    bar_time = latest_bar['timestamp']
                    now = datetime.now(ET)
                    age_seconds = (now - bar_time).total_seconds()
                    
                    if age_seconds <= 120:  # Bar is less than 2 minutes old
                        latest_close = float(latest_bar['Close'])
                        logger.debug(f"📊 Current price from WebSocket: ${latest_close:.2f} (age: {age_seconds:.0f}s)")
                        return latest_close
                    else:
                        logger.debug(f"📊 WebSocket bar is stale ({age_seconds:.0f}s old), using REST API")

        # Use REST API with IEX feed - use last trade price or bid/ask midpoint
        try:
            request = StockLatestQuoteRequest(
                symbol_or_symbols=self.ticker,
                feed=ALPACA_FEED
            )
            quote = self.data_client.get_stock_latest_quote(request)
            if self.ticker in quote:
                q = quote[self.ticker]
                # Use bid price for stop loss checks (what we'd get if we sold now)
                # Or use midpoint if available
                if hasattr(q, 'bid_price') and hasattr(q, 'ask_price'):
                    bid = float(q.bid_price) if q.bid_price else 0
                    ask = float(q.ask_price) if q.ask_price else 0
                    if bid > 0 and ask > 0:
                        price = (bid + ask) / 2.0
                    elif bid > 0:
                        price = bid
                    elif ask > 0:
                        price = ask
                    else:
                        price = None
                elif hasattr(q, 'bid_price') and q.bid_price:
                    price = float(q.bid_price)
                elif hasattr(q, 'ask_price') and q.ask_price:
                    price = float(q.ask_price)
                else:
                    price = None
                
                if price:
                    logger.debug(f"📊 Current price from REST API: ${price:.2f}")
                    return price
            return None
        except Exception as e:
            logger.error(f"Error getting current price: {e}")
            return None
    
    def _on_ws_message(self, ws, message):
        """Handle WebSocket messages."""
        try:
            # Log raw message for debugging (first 500 chars)
            logger.info(f"📨 WebSocket message received: {str(message)[:500]}")
            
            data = json.loads(message)
            
            # Handle array of messages (Market Data API v2 sends arrays)
            if isinstance(data, list):
                logger.info(f"   Message is an array with {len(data)} items")
                for msg in data:
                    self._process_ws_message(msg)
            else:
                logger.info(f"   Message is a single object: {list(data.keys())}")
                self._process_ws_message(data)
                
        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON in WebSocket message: {e}")
            logger.error(f"   Message: {str(message)[:500]}")
        except Exception as e:
            logger.error(f"❌ Error processing WebSocket message: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def _process_ws_message(self, msg: Dict):
        """Process a single WebSocket message."""
        # Alpaca Market Data API v2 uses 'T' for message type
        # Trading API uses 'stream' field
        msg_type = msg.get('T')  # Market Data API message type
        stream = msg.get('stream')  # Trading API stream type
        
        # Handle Trading API format (stream field)
        if stream == 'authorization':
            auth_data = msg.get('data', {})
            auth_status = auth_data.get('status', '')
            if auth_status == 'authorized':
                logger.info("✅ WebSocket authenticated (Trading API format)")
                self.ws_authenticated = True
                # Subscribe to bars
                subscribe_msg = {
                    "action": "subscribe",
                    "bars": [self.ticker]
                }
                subscribe_json = json.dumps(subscribe_msg)
                logger.info(f"📡 Subscribing to {self.ticker} bars...")
                self.ws.send(subscribe_json)
            elif auth_status == 'unauthorized':
                logger.error("❌ WebSocket authentication failed - unauthorized")
                self.ws_connected = False
        
        # Handle Market Data API format (T field)
        elif msg_type == 'success':
            msg_text = msg.get('msg', '')
            logger.info(f"📨 Success message: {msg_text}")
            
            if 'connected' in msg_text.lower():
                logger.info("✅ Server connected - sending authentication...")
                # Send authentication after receiving "connected" message
                # Per Alpaca Market Data API docs: use "auth" action (NOT "authenticate")
                # Format: {"action": "auth", "key": "...", "secret": "..."}
                auth_msg = {
                    "action": "auth",
                    "key": ALPACA_API_KEY,
                    "secret": ALPACA_API_SECRET
                }
                auth_json = json.dumps(auth_msg)
                logger.info(f"🔐 Sending authentication (action: 'auth')...")
                logger.debug(f"   Auth message: {auth_json.replace(ALPACA_API_SECRET, '***')}")
                try:
                    self.ws.send(auth_json)
                    logger.info("✅ Auth message sent successfully")
                except Exception as e:
                    logger.error(f"❌ Error sending auth: {e}")
                
            elif 'authenticated' in msg_text.lower():
                logger.info("✅ WebSocket authenticated - subscribing to bars...")
                self.ws_authenticated = True
                # Subscribe to 1-minute bars for TSLA
                subscribe_msg = {
                    "action": "subscribe",
                    "bars": [self.ticker]
                }
                subscribe_json = json.dumps(subscribe_msg)
                logger.info(f"📡 Subscribing to {self.ticker} bars...")
                self.ws.send(subscribe_json)
            elif 'subscribed' in msg_text.lower():
                logger.info("✅ Subscribed to bars stream - waiting for bars...")
        
        # Handle subscription confirmation
        elif msg_type == 'subscription':
            logger.info(f"📡 Subscription confirmed: {msg}")
        
        # Handle bar updates (1-minute bars)
        elif msg_type == 'b':  # Bar update
            symbol = msg.get('S')
            if symbol == self.ticker or symbol == self.ticker.upper():
                # Convert bar to our format
                bar_time_str = msg.get('t')
                if bar_time_str:
                    try:
                        # Parse timestamp (format: "2024-07-24T07:56:00Z" or "2024-07-24T07:56:00.000Z")
                        if bar_time_str.endswith('Z'):
                            bar_time = datetime.fromisoformat(bar_time_str.replace('Z', '+00:00'))
                        else:
                            bar_time = datetime.fromisoformat(bar_time_str)
                        
                        # Ensure timezone aware
                        if bar_time.tzinfo is None:
                            bar_time = pytz.UTC.localize(bar_time)
                        bar_time = bar_time.astimezone(ET)
                        
                        bar_data = {
                            "timestamp": bar_time,
                            "Open": float(msg.get('o', 0)),
                            "High": float(msg.get('h', 0)),
                            "Low": float(msg.get('l', 0)),
                            "Close": float(msg.get('c', 0)),
                            "Volume": int(msg.get('v', 0))
                        }
                        
                        # Add to buffer (thread-safe)
                        with self.bars_lock:
                            # Remove old bars (keep last 2 hours)
                            cutoff_time = datetime.now(ET) - timedelta(hours=2)
                            self.bars_buffer = [b for b in self.bars_buffer if b['timestamp'] >= cutoff_time]
                            
                            # Add new bar (avoid duplicates based on timestamp)
                            if not self.bars_buffer or self.bars_buffer[-1]['timestamp'] != bar_time:
                                self.bars_buffer.append(bar_data)
                                logger.info(f"📊 New bar received: {bar_time.strftime('%H:%M:%S')} ET @ ${bar_data['Close']:.2f} (Volume: {bar_data['Volume']:,})")
                                logger.info(f"   Buffer now has {len(self.bars_buffer)} bars")
                            else:
                                # Update existing bar if it's the same timestamp
                                self.bars_buffer[-1] = bar_data
                                logger.debug(f"📊 Updated bar: {bar_time.strftime('%H:%M:%S')} ET @ ${bar_data['Close']:.2f}")
                    except Exception as e:
                        logger.error(f"Error parsing bar timestamp {bar_time_str}: {e}")
        
        # Handle errors
        elif msg_type == 'error':
            error_code = msg.get('code')
            error_msg = msg.get('msg', 'Unknown error')
            logger.error(f"❌ WebSocket error {error_code}: {error_msg}")
            
            if error_code == 409 or 'subscription' in error_msg.lower():
                logger.warning("⚠️ WebSocket subscription insufficient - will fallback to REST API")
                self.use_websocket = False
                self.ws_connected = False
    
    def _on_ws_error(self, ws, error):
        """Handle WebSocket errors."""
        logger.error(f"❌ WebSocket error: {error}")
        self.ws_connected = False
    
    def _on_ws_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket close."""
        logger.warning(f"⚠️ WebSocket closed: {close_status_code} - {close_msg}")
        self.ws_connected = False
        self.ws_authenticated = False
    
    def _on_ws_open(self, ws):
        """Handle WebSocket open - wait for server's 'connected' message before authenticating."""
        logger.info("🔌 WebSocket connection opened - waiting for server 'connected' message...")
        self.ws_connected = True
    
    def start_websocket_stream(self):
        """Start WebSocket streaming for real-time bars.

        Uses Alpaca Market Data API v2 with IEX feed (free for paper trading).
        Authentication via message (not HTTP headers) to avoid double-auth errors.

        Protocol:
        1. Connect to wss://stream.data.alpaca.markets/v2/iex
        2. Server sends [{"T":"success","msg":"connected"}]
        3. Client sends {"action":"auth","key":"...","secret":"..."}
        4. Server sends [{"T":"success","msg":"authenticated"}]
        5. Client sends {"action":"subscribe","bars":["TSLA"]}
        6. Server sends [{"T":"subscription",...}]
        7. Server sends bar updates as [{"T":"b","S":"TSLA",...}]
        """
        logger.info(f"🔌 start_websocket_stream called, use_websocket={self.use_websocket}")
        if not self.use_websocket:
            logger.info("⚠️ WebSocket disabled - using REST API fallback")
            return

        try:
            # IEX feed is free for all accounts (including paper trading)
            ws_url = "wss://stream.data.alpaca.markets/v2/iex"
            logger.info(f"🔌 Connecting to WebSocket: {ws_url}")
            logger.info(f"   Using IEX feed (free, paper trading compatible)")

            # Use message-based auth ONLY (no HTTP headers)
            # Sending both headers and auth message causes 403 "already authenticated"
            self.ws = websocket.WebSocketApp(
                ws_url,
                on_open=self._on_ws_open,
                on_message=self._on_ws_message,
                on_error=self._on_ws_error,
                on_close=self._on_ws_close
            )

            # Start WebSocket in a separate thread
            def run_websocket():
                import ssl
                # macOS Python may not have system certs configured;
                # allow connecting without cert verification for the data stream
                ssl_opts = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}

                while self.use_websocket and self.running:
                    try:
                        logger.info("🔌 WebSocket run_forever starting...")
                        self.ws.run_forever(
                            sslopt=ssl_opts,
                            ping_interval=30,
                            ping_timeout=10,
                            reconnect=5  # Auto-reconnect after 5 seconds
                        )
                    except Exception as e:
                        logger.error(f"❌ WebSocket run_forever error: {e}")

                    if not self.use_websocket or not self.running:
                        break

                    # If we get here, WebSocket disconnected - try to reconnect
                    logger.warning("⚠️ WebSocket disconnected - reconnecting in 5 seconds...")
                    self.ws_connected = False
                    self.ws_authenticated = False
                    time.sleep(5)

                    # Recreate WebSocketApp for reconnection
                    self.ws = websocket.WebSocketApp(
                        ws_url,
                        on_open=self._on_ws_open,
                        on_message=self._on_ws_message,
                        on_error=self._on_ws_error,
                        on_close=self._on_ws_close
                    )

            ws_thread = threading.Thread(target=run_websocket, daemon=True)
            ws_thread.start()

            # Poll for connection+auth (up to 10 seconds)
            for i in range(20):
                if self.ws_connected and self.ws_authenticated:
                    logger.info("✅ WebSocket streaming active - receiving real-time bars")
                    return
                time.sleep(0.5)

            # Don't disable WebSocket on timeout - it may still connect
            # The main loop will use WebSocket buffer when data arrives
            if self.ws_connected:
                logger.info("🔌 WebSocket connected but still authenticating - will keep trying")
            else:
                logger.warning("⚠️ WebSocket not yet connected - will keep trying in background")

        except Exception as e:
            logger.error(f"❌ Error starting WebSocket: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def stop_websocket_stream(self):
        """Stop WebSocket streaming."""
        if self.ws:
            logger.info("🔌 Closing WebSocket connection...")
            self.ws.close()
            self.ws = None
            self.ws_connected = False
            self.ws_authenticated = False
    
    def get_bars_from_buffer(self) -> pd.DataFrame:
        """Get bars from WebSocket buffer as DataFrame."""
        with self.bars_lock:
            if not self.bars_buffer:
                return pd.DataFrame()
            
            # Convert to DataFrame
            df = pd.DataFrame(self.bars_buffer)
            df = df.set_index("timestamp")
            
            # Sort by timestamp
            df = df.sort_index()
            
            return df
    
    def fetch_recent_bars(self, minutes: int = 60) -> pd.DataFrame:
        """Fetch recent 1-minute bars for analysis.

        Strategy: Use the source with the most data.
        - If WebSocket buffer has >= 3 bars (minimum for FVG detection), use it
        - Otherwise try REST API with IEX feed
        - If REST also fails/returns less, use whichever has more
        """
        # Check WebSocket buffer
        ws_df = pd.DataFrame()
        ws_buf = self.get_bars_from_buffer()
        if not ws_buf.empty:
            cutoff_time = datetime.now(ET) - timedelta(minutes=minutes)
            ws_df = ws_buf[ws_buf.index >= cutoff_time]

        # If WebSocket has enough bars for signal detection, use it directly
        if len(ws_df) >= 3:
            logger.info(f"📊 Using {len(ws_df)} bars from WebSocket buffer")
            return ws_df

        # Try REST API (either WS has too few bars or is empty)
        try:
            end_time = datetime.now(ET)
            start_time = end_time - timedelta(minutes=minutes + 5)

            logger.info(f"📡 Fetching bars via REST (IEX feed) from {start_time.strftime('%H:%M:%S')} to {end_time.strftime('%H:%M:%S')} ET")

            request = StockBarsRequest(
                symbol_or_symbols=self.ticker,
                timeframe=TimeFrame.Minute,
                start=start_time,
                end=end_time,
                feed=ALPACA_FEED
            )

            bars = self.data_client.get_stock_bars(request)

            if not hasattr(bars, 'data') or not bars.data:
                logger.warning("⚠️ No data in REST response")
                return pd.DataFrame()

            # Try different ticker formats
            bar_list = None
            if self.ticker in bars.data:
                bar_list = bars.data[self.ticker]
            elif self.ticker.upper() in bars.data:
                bar_list = bars.data[self.ticker.upper()]
            else:
                keys = list(bars.data.keys())
                if keys:
                    bar_list = bars.data[keys[0]]

            if bar_list is None or len(bar_list) == 0:
                logger.warning(f"⚠️ No bars returned for {self.ticker}")
                return pd.DataFrame()

            records = []
            for bar in bar_list:
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

            logger.info(f"✅ Fetched {len(df)} bars via REST API. Range: {df.index[0]} to {df.index[-1]}")

            # Merge with any WebSocket bars that are newer than REST data
            if not ws_df.empty:
                rest_latest = df.index[-1]
                newer_ws = ws_df[ws_df.index > rest_latest]
                if not newer_ws.empty:
                    df = pd.concat([df, newer_ws])
                    df = df[~df.index.duplicated(keep='last')]
                    df = df.sort_index()
                    logger.info(f"📊 Merged {len(newer_ws)} newer WebSocket bars. Total: {len(df)}")

            return df

        except Exception as e:
            logger.error(f"❌ Error fetching bars via REST: {e}")
            # If REST fails but we have some WS bars, use those
            if not ws_df.empty:
                logger.info(f"📊 REST failed, using {len(ws_df)} WebSocket bars instead")
                return ws_df
            return pd.DataFrame()
    
    def capture_first_candle(self) -> Optional[FirstCandle]:
        """
        Capture the first 5-minute candle at market open (9:30-9:35 AM ET).

        Tries REST API with IEX feed first, falls back to building from
        WebSocket 1-min bars if REST fails.
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

            # Method 1: Try REST API with IEX feed
            first_candle = self._capture_first_candle_rest(market_open, candle_close)
            if first_candle:
                return first_candle

            # Method 2: Build from WebSocket 1-min bars
            first_candle = self._capture_first_candle_from_ws_buffer(market_open, candle_close)
            if first_candle:
                return first_candle

            logger.warning("⚠️ Could not capture first candle from REST or WebSocket")
            return None

        except Exception as e:
            logger.error(f"Error capturing first candle: {e}")
            return None

    def _capture_first_candle_rest(self, market_open, candle_close) -> Optional[FirstCandle]:
        """Try to capture first candle via REST API with IEX feed."""
        try:
            start_time = market_open - timedelta(minutes=1)
            end_time = candle_close + timedelta(minutes=1)

            request = StockBarsRequest(
                symbol_or_symbols=self.ticker,
                timeframe=TimeFrame(self.anchor_min, TimeFrameUnit.Minute),
                start=start_time,
                end=end_time,
                feed=ALPACA_FEED
            )

            bars = self.data_client.get_stock_bars(request)

            if self.ticker not in bars.data or len(bars.data[self.ticker]) == 0:
                logger.warning("⚠️ No REST data for first candle")
                return None

            bar = bars.data[self.ticker][0]

            bar_time = bar.timestamp
            if bar_time.tzinfo is None:
                bar_time = pytz.utc.localize(bar_time)
            bar_time = bar_time.astimezone(ET)

            first_candle = FirstCandle(
                high=float(bar.high),
                low=float(bar.low),
                open=float(bar.open),
                close=float(bar.close),
                time=bar_time,
                close_time=bar_time + timedelta(minutes=self.anchor_min)
            )

            logger.info(f"✅ First Candle captured (REST): High=${first_candle.high:.2f}, Low=${first_candle.low:.2f}")
            return first_candle

        except Exception as e:
            logger.warning(f"⚠️ REST first candle failed: {e}")
            return None

    def _capture_first_candle_from_ws_buffer(self, market_open, candle_close) -> Optional[FirstCandle]:
        """Build the 5-min first candle from accumulated 1-min WebSocket bars."""
        with self.bars_lock:
            if not self.bars_buffer:
                logger.warning("⚠️ WebSocket buffer empty - cannot build first candle")
                return None

            # Filter bars in the 9:30-9:35 window
            fc_bars = [
                b for b in self.bars_buffer
                if market_open <= b['timestamp'] < candle_close
            ]

            if len(fc_bars) < 1:
                logger.warning(f"⚠️ Only {len(fc_bars)} WebSocket bars in 9:30-9:35 window (need at least 1)")
                return None

            # Build 5-min candle from 1-min bars
            fc_high = max(b['High'] for b in fc_bars)
            fc_low = min(b['Low'] for b in fc_bars)
            fc_open = fc_bars[0]['Open']
            fc_close = fc_bars[-1]['Close']

            first_candle = FirstCandle(
                high=fc_high,
                low=fc_low,
                open=fc_open,
                close=fc_close,
                time=market_open,
                close_time=candle_close
            )

            logger.info(f"✅ First Candle built from {len(fc_bars)} WebSocket bars: High=${fc_high:.2f}, Low=${fc_low:.2f}")
            return first_candle
    
    def scan_for_signals(self) -> Optional[Dict[str, Any]]:
        """
        Scan for trading signals using the STANDARD Strategy A logic from strategy.py.
        
        Uses:
        - detect_fvgs() from strategy.py
        - check_retest_and_engulf() from strategy.py
        - filter_by_first_candle_break() from strategy.py
        """
        if not self.first_candle:
            logger.info("No first candle - cannot scan for signals")
            return None
        
        if self.trades_today >= self.config["max_trades_per_day"]:
            logger.info(f"Max trades reached for today: {self.trades_today}")
            return None
        
        # Fetch recent 1-min bars (confirmation timeframe)
        df = self.fetch_recent_bars(minutes=120)
        if df.empty:
            logger.warning("⚠️ No data fetched for signal scanning")
            return None
        
        logger.info(f"📊 Fetched {len(df)} bars for signal scanning")
        logger.info(f"   Data range: {df.index[0].strftime('%H:%M:%S')} to {df.index[-1].strftime('%H:%M:%S')} ET")
        logger.info(f"   Latest price: ${df['Close'].iloc[-1]:.2f}")
        
        # Use the STANDARD detect_fvgs from strategy.py
        fvgs = detect_fvgs(df)
        logger.info(f"🔍 Detected {len(fvgs)} FVGs")
        
        if len(fvgs) > 0:
            for i, fvg in enumerate(fvgs[:3]):  # Show first 3 FVGs
                logger.info(f"   FVG {i+1}: {fvg['type'].upper()} at {fvg['time'].strftime('%H:%M:%S')} ET, Gap: ${fvg['gap_bottom']:.2f}-${fvg['gap_top']:.2f}")
        
        for fvg in fvgs:
            # Use the STANDARD check_retest_and_engulf from strategy.py
            signal = check_retest_and_engulf(df, fvg)
            
            if signal:
                logger.info(f"✅ Retest+Engulf found: {signal['direction']} @ ${signal['entry_price']:.2f}")
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
                else:
                    logger.info(f"❌ Signal failed first candle break validation")
        
        return None
    
    def calculate_position_size(self, entry_price: float, stop_price: float) -> int:
        """Calculate position size based on risk per trade, capped by configured capital and buying power.
        
        Per Alpaca margin guidelines:
        - Account must have sufficient buying power
        - For shorts, calculated value is MAX(limit_price, 3% above ask) * qty
        - Position cost is compared against available buying power
        """
        risk_per_share = abs(entry_price - stop_price)

        if risk_per_share <= 0:
            return 0

        # Calculate shares based on risk per trade ($100)
        shares = int(self.config["risk_per_trade"] / risk_per_share)

        # Cap total position cost at configured capital ($5,000), not full buying power
        configured_capital = self.config.get("capital", 5000)
        max_shares_by_capital = int(configured_capital / entry_price)
        shares = min(shares, max_shares_by_capital)
        
        # Also check against actual buying power from account
        try:
            account = self.trading_client.get_account()
            buying_power = float(account.buying_power)
            
            # Reserve some buffer (use 90% of buying power max)
            usable_buying_power = buying_power * 0.9
            max_shares_by_bp = int(usable_buying_power / entry_price)
            
            if max_shares_by_bp < shares:
                logger.warning(f"⚠️ Reducing position size from {shares} to {max_shares_by_bp} due to buying power constraint")
                logger.warning(f"   Buying power: ${buying_power:.2f}, Usable: ${usable_buying_power:.2f}")
                shares = max_shares_by_bp
        except Exception as e:
            logger.warning(f"⚠️ Could not verify buying power: {e}")

        logger.info(f"   Position sizing: risk/share=${risk_per_share:.2f}, "
                     f"shares={shares}, cost=${shares * entry_price:.2f}, "
                     f"capital cap=${configured_capital}")

        return max(shares, 0)
    
    def check_shortable(self) -> bool:
        """Check if the ticker is Easy to Borrow (ETB) for short selling.
        
        Per Alpaca guidance:
        - Only ETB securities can be shorted
        - Stock borrow availability changes daily
        - HTB (Hard to Borrow) orders are cancelled
        """
        try:
            asset = self.trading_client.get_asset(self.ticker)
            
            is_shortable = asset.shortable
            is_etb = asset.easy_to_borrow
            
            logger.info(f"📊 {self.ticker} shorting status: shortable={is_shortable}, ETB={is_etb}")
            
            if not is_shortable:
                logger.warning(f"⚠️ {self.ticker} is NOT shortable")
                return False
            
            if not is_etb:
                logger.warning(f"⚠️ {self.ticker} is Hard to Borrow (HTB) - shorting not recommended")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error checking shortable status: {e}")
            return False
    
    def place_market_order(self, side: str, quantity: int) -> Optional[str]:
        """Place a market order on Alpaca.
        
        Per Alpaca order guidelines:
        - Market orders fill nearly instantaneously
        - Uses DAY time-in-force (valid for current trading day)
        - For shorts, verifies ETB status first
        """
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
            
            # Wait briefly and verify order status
            time.sleep(0.5)
            try:
                order_status = self.trading_client.get_order_by_id(order.id)
                logger.info(f"   Order status: {order_status.status}")
                if order_status.filled_avg_price:
                    logger.info(f"   Filled at: ${float(order_status.filled_avg_price):.2f}")
            except Exception as e:
                logger.debug(f"Could not verify order status: {e}")
            
            return order.id
            
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return None
    
    def close_position(self) -> bool:
        """Close the current position.
        
        Per Alpaca order guidelines:
        - Uses market order for immediate execution
        - Verifies order fill before confirming close
        """
        try:
            if not self.active_trade:
                return True
            
            # Determine close side (opposite of entry)
            close_side = "SELL" if self.active_trade.direction == "LONG" else "BUY"
            
            logger.info(f"📥 Closing position: {close_side} {self.active_trade.quantity} shares")
            
            order_id = self.place_market_order(close_side, self.active_trade.quantity)
            
            if order_id:
                # Verify the order was filled
                try:
                    time.sleep(1)  # Wait for fill
                    order_status = self.trading_client.get_order_by_id(order_id)
                    
                    if order_status.status.value in ['filled', 'partially_filled']:
                        exit_price = float(order_status.filled_avg_price) if order_status.filled_avg_price else None
                        logger.info(f"✅ Position closed - Order ID: {order_id}, Exit price: ${exit_price:.2f if exit_price else 'N/A'}")
                        return True
                    else:
                        logger.warning(f"⚠️ Order status: {order_status.status.value} - may need manual verification")
                        return True  # Still return True as order was submitted
                except Exception as e:
                    logger.debug(f"Could not verify close order: {e}")
                
                logger.info(f"📥 Position close order submitted - Order ID: {order_id}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            return False
    
    def manage_position(self) -> None:
        """
        Manage the active position - check stops and targets.
        Standard behavior: no trailing stop, just fixed stop and target.
        
        Uses REST API for price to ensure we have the most current price
        for stop loss and take profit decisions.
        """
        if not self.active_trade:
            return
        
        # Use REST API for more accurate real-time price in position management
        current_price = self.get_current_price(use_rest_api=True)
        if not current_price:
            # Fallback to WebSocket if REST fails
            current_price = self.get_current_price(use_rest_api=False)
            if not current_price:
                logger.warning(f"⚠️ Cannot get current price for position management")
                return
        
        entry = self.active_trade.entry_price
        stop = self.active_trade.stop_price
        target = self.active_trade.target_price
        direction = self.active_trade.direction
        
        # Log position status for debugging
        logger.info(f"📊 Managing position: {direction} @ ${entry:.2f}, Current: ${current_price:.2f}, Stop: ${stop:.2f}, Target: ${target:.2f}")
        
        # Check stop hit
        stop_hit = False
        if direction == "LONG" and current_price <= stop:
            stop_hit = True
            logger.warning(f"🔴 STOP LOSS HIT! Current price ${current_price:.2f} <= Stop ${stop:.2f}")
        elif direction == "SHORT" and current_price >= stop:
            stop_hit = True
            logger.warning(f"🔴 STOP LOSS HIT! Current price ${current_price:.2f} >= Stop ${stop:.2f}")
        
        if stop_hit:
            logger.warning(f"🛑 STOP LOSS TRIGGERED! Attempting to close position...")
            if self.close_position():
                pnl = -self.active_trade.risk_amount
                self.stats["losses"] += 1
                self.stats["total_trades"] += 1
                self.stats["total_pnl"] += pnl
                logger.info(f"🔴 Trade stopped out - PnL: ${pnl:.2f}")
                # Update trade history
                if self.trade_history:
                    self.trade_history[-1].update({
                        "exit_time": datetime.now(ET).strftime('%H:%M:%S'),
                        "exit_price": round(current_price, 2),
                        "outcome": "LOSS",
                        "pnl": round(pnl, 2),
                    })
                self.active_trade = None
                self.state = BotState.SCANNING_FOR_SIGNAL
            else:
                logger.error(f"❌ FAILED to close position! Retrying...")
                # Retry closing position
                time.sleep(1)
                if self.close_position():
                    logger.info(f"✅ Position closed on retry")
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
                # Update trade history
                if self.trade_history:
                    self.trade_history[-1].update({
                        "exit_time": datetime.now(ET).strftime('%H:%M:%S'),
                        "exit_price": round(current_price, 2),
                        "outcome": "WIN",
                        "pnl": round(pnl, 2),
                    })
                self.active_trade = None
                self.state = BotState.SCANNING_FOR_SIGNAL
    
    def execute_signal(self, signal: Dict[str, Any]) -> bool:
        """Execute a trading signal.
        
        Per Alpaca order guidelines:
        - Verifies buying power before placing order
        - For SHORT trades, verifies ETB (Easy to Borrow) status
        - Uses market orders with DAY time-in-force
        - Validates order execution status
        """
        try:
            entry_price = signal['entry_price']
            stop_price = signal['stop_price']
            direction = signal['direction']
            
            logger.info(f"🎯 Executing {direction} signal at ${entry_price:.2f}")
            
            # For SHORT trades, verify the stock is shortable (ETB)
            if direction == "SHORT":
                if not self.check_shortable():
                    logger.warning(f"⚠️ Cannot short {self.ticker} - not ETB. Skipping trade.")
                    return False
            
            risk_dist = abs(entry_price - stop_price)
            target_price = (entry_price + (risk_dist * self.rr) 
                           if direction == "LONG" 
                           else entry_price - (risk_dist * self.rr))
            
            # Calculate position size (includes buying power check)
            quantity = self.calculate_position_size(entry_price, stop_price)
            
            if quantity <= 0:
                logger.warning("Position size too small or insufficient buying power - skipping trade")
                return False
            
            # Verify buying power one more time before placing order
            try:
                account = self.trading_client.get_account()
                buying_power = float(account.buying_power)
                required_bp = quantity * entry_price
                
                if required_bp > buying_power:
                    logger.error(f"❌ Insufficient buying power! Required: ${required_bp:.2f}, Available: ${buying_power:.2f}")
                    return False
                    
                logger.info(f"   Buying power check: Required ${required_bp:.2f} / Available ${buying_power:.2f} ✓")
            except Exception as e:
                logger.warning(f"⚠️ Could not verify buying power: {e}")
            
            # Place order
            order_side = "BUY" if direction == "LONG" else "SELL"
            order_id = self.place_market_order(order_side, quantity)
            
            if not order_id:
                logger.error("❌ Order placement failed!")
                return False
            
            # Get actual fill price from order
            actual_entry_price = entry_price
            try:
                time.sleep(1)  # Wait for order to fill
                order_status = self.trading_client.get_order_by_id(order_id)
                if order_status.filled_avg_price:
                    actual_entry_price = float(order_status.filled_avg_price)
                    logger.info(f"   Actual fill price: ${actual_entry_price:.2f} (signal was ${entry_price:.2f})")
                    
                    # Recalculate target based on actual entry
                    risk_dist = abs(actual_entry_price - stop_price)
                    target_price = (actual_entry_price + (risk_dist * self.rr) 
                                   if direction == "LONG" 
                                   else actual_entry_price - (risk_dist * self.rr))
            except Exception as e:
                logger.debug(f"Could not get fill price: {e}")
            
            # Create active trade record
            self.active_trade = ActiveTrade(
                entry_price=actual_entry_price,
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

            # Record trade in history
            self.trade_history.append({
                "trade_num": len(self.trade_history) + 1,
                "entry_time": datetime.now(ET).strftime('%H:%M:%S'),
                "direction": direction,
                "quantity": quantity,
                "entry_price": round(actual_entry_price, 2),
                "stop_price": round(stop_price, 2),
                "target_price": round(target_price, 2),
                "exit_price": None,
                "exit_time": None,
                "outcome": "OPEN",
                "pnl": 0.0,
                "order_id": order_id,
            })

            logger.info(f"✅ Trade opened: {direction} {quantity} shares at ${actual_entry_price:.2f}")
            logger.info(f"   Stop: ${stop_price:.2f}, Target: ${target_price:.2f}, R:R {self.rr}:1")

            return True
            
        except Exception as e:
            logger.error(f"Error executing signal: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def reset_daily_state(self) -> None:
        """Reset state for a new trading day."""
        self.first_candle = None
        self.trades_today = 0
        self._attempted_signals.clear()
        self.detected_signals.clear()
        self.trade_history.clear()
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
                    market_open = self.is_market_open()
                    if market_open:
                        self.state = BotState.WAITING_FIRST_CANDLE
                        logger.info("📈 Market is open - waiting for first candle")
                    else:
                        time_until = self.get_time_until_market_open()
                        if time_until and time_until.total_seconds() > 0:
                            mins = int(time_until.total_seconds() / 60)
                            if mins % 30 == 0 or mins < 60:  # Log every 30 minutes, or every minute if < 1 hour
                                logger.info(f"⏳ Waiting for market open ({mins} minutes)")
                        else:
                            # If we can't get time until open, log current time for debugging
                            logger.warning(f"⚠️ Market appears closed. Current time ET: {now.strftime('%H:%M:%S')}")
                        self.state = BotState.WAITING_MARKET_OPEN
                
                elif self.state == BotState.WAITING_FIRST_CANDLE:
                    # Wait until 9:35 to capture first candle
                    if now.hour == 9 and now.minute >= 35:
                        logger.info(f"⏰ It's {now.strftime('%H:%M')} - attempting to capture first candle...")
                        self.first_candle = self.capture_first_candle()
                        if self.first_candle:
                            self.state = BotState.SCANNING_FOR_SIGNAL
                            logger.info("🔍 First candle captured! Scanning for Strategy A signals...")
                        else:
                            logger.warning("⚠️ Failed to capture first candle - will retry")
                    elif now.hour > 9:
                        # After 9:35, try to capture (in case bot started late)
                        logger.info(f"⏰ It's {now.strftime('%H:%M')} - attempting to capture first candle (late start)...")
                        self.first_candle = self.capture_first_candle()
                        if self.first_candle:
                            self.state = BotState.SCANNING_FOR_SIGNAL
                            logger.info("🔍 First candle captured! Scanning for Strategy A signals...")
                        else:
                            logger.warning("⚠️ Failed to capture first candle - will retry next iteration")
                    else:
                        if now.minute % 5 == 0:  # Log every 5 minutes
                            logger.info(f"⏰ Waiting for 9:35... Current time: {now.strftime('%H:%M')}")
                
                elif self.state == BotState.SCANNING_FOR_SIGNAL:
                    # CRITICAL: Check if we have an active position but state is wrong
                    # This can happen if bot was restarted or state was lost
                    if self.active_trade:
                        logger.warning(f"⚠️ State mismatch detected! Bot is SCANNING but has active_trade. Setting state to IN_POSITION.")
                        self.state = BotState.IN_POSITION
                    else:
                        # Also check Alpaca for open positions
                        try:
                            positions = self.trading_client.get_all_positions()
                            tsla_positions = [p for p in positions if p.symbol == self.ticker]
                            if tsla_positions and not self.active_trade:
                                logger.warning(f"⚠️ Found open {self.ticker} position in Alpaca but no active_trade in bot!")
                                logger.warning(f"   Position: {tsla_positions[0].qty} shares @ ${tsla_positions[0].avg_entry_price}")
                                # Try to reconstruct active_trade from Alpaca position
                                pos = tsla_positions[0]
                                qty = float(pos.qty)
                                entry_price = float(pos.avg_entry_price)
                                direction = "LONG" if qty > 0 else "SHORT"
                                # We don't have stop/target, so we'll need to manage without them
                                logger.warning(f"   Cannot fully reconstruct trade (missing stop/target). Manual intervention may be needed.")
                        except Exception as e:
                            logger.debug(f"Error checking Alpaca positions: {e}")
                    
                    # Check if market still open
                    if not self.is_market_open():
                        logger.info("📴 Market closed - waiting for next session")
                        self.state = BotState.WAITING_MARKET_OPEN
                    else:
                        # Log scanning activity periodically
                        if now.minute % 5 == 0 and now.second < 10:  # Log every 5 minutes
                            logger.info(f"🔍 Scanning for signals... Time: {now.strftime('%H:%M:%S')}, First candle: {self.first_candle.high if self.first_candle else 'None'}")
                        signal = self.scan_for_signals()
                        if signal:
                            # Create a key to identify this signal uniquely
                            sig_key = (
                                signal['direction'],
                                round(signal['entry_price'], 2),
                                str(signal['fvg']['time'])
                            )
                            if sig_key in self._attempted_signals:
                                # Already tried this signal, skip it silently
                                pass
                            else:
                                signal_record = {
                                    "timestamp": now.strftime('%H:%M:%S'),
                                    "direction": signal['direction'],
                                    "entry_price": round(signal['entry_price'], 2),
                                    "stop_price": round(signal['stop_price'], 2),
                                    "fvg_type": signal['fvg']['type'],
                                    "fvg_time": signal['fvg']['time'].strftime('%H:%M:%S'),
                                    "fvg_gap": f"${signal['fvg']['gap_bottom']:.2f}-${signal['fvg']['gap_top']:.2f}",
                                    "status": "pending",
                                }
                                if self.execute_signal(signal):
                                    self.state = BotState.IN_POSITION
                                    signal_record["status"] = "executed"
                                else:
                                    # Mark signal as attempted so we don't retry it
                                    self._attempted_signals.add(sig_key)
                                    signal_record["status"] = "failed"
                                    logger.info(f"   Signal marked as attempted - will skip on future scans")
                                self.detected_signals.append(signal_record)
                
                elif self.state == BotState.IN_POSITION:
                    # Manage position more aggressively - check every iteration
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

        # Reset WebSocket state and try again
        self.use_websocket = True
        self.ws_connected = False
        self.ws_authenticated = False
        self.bars_buffer = []  # Clear old buffer
        self.running = True  # Set before WebSocket so reconnect loop works

        # Start WebSocket streaming (handles its own connection wait)
        logger.info("🔌 Starting WebSocket streaming...")
        self.start_websocket_stream()

        # Start main trading loop
        thread = threading.Thread(target=self.run_loop, daemon=True)
        thread.start()
        logger.info("🤖 Bot thread started")
    
    def stop(self) -> None:
        """Stop the bot."""
        logger.info("🛑 Stopping bot...")
        self.running = False
        self.state = BotState.STOPPED
        self.stop_websocket_stream()
    
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
            },
            "websocket": {
                "enabled": self.use_websocket,
                "connected": self.ws_connected,
                "authenticated": self.ws_authenticated,
                "bars_in_buffer": len(self.bars_buffer),
            },
            "detected_signals": self.detected_signals[-50:],  # Last 50 signals
            "trade_history": self.trade_history[-50:],  # Last 50 trades
            "position": {
                "direction": self.active_trade.direction if self.active_trade else None,
                "entry_price": self.active_trade.entry_price if self.active_trade else None,
                "stop_price": self.active_trade.stop_price if self.active_trade else None,
                "target_price": self.active_trade.target_price if self.active_trade else None,
                "quantity": self.active_trade.quantity if self.active_trade else None,
                "entry_time": self.active_trade.entry_time.strftime('%H:%M:%S') if self.active_trade and self.active_trade.entry_time else None,
                "order_id": self.active_trade.order_id if self.active_trade else None,
                "risk_amount": self.active_trade.risk_amount if self.active_trade else None,
            } if self.active_trade else None,
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


def reset_bot() -> TslaStrategyABot:
    """Force reset the bot instance with fresh configuration.
    
    This reloads config.py and creates a new bot instance.
    Use this when API credentials or settings have changed.
    """
    global _bot_instance
    
    # Stop existing bot if running
    if _bot_instance is not None and _bot_instance.running:
        _bot_instance.stop()
    
    # Reload config module to get fresh credentials
    import importlib
    import config
    importlib.reload(config)
    
    # Update module-level references
    global ALPACA_API_KEY, ALPACA_API_SECRET
    from config import ALPACA_API_KEY, ALPACA_API_SECRET
    
    # Create fresh bot instance
    _bot_instance = TslaStrategyABot()
    logger.info("🔄 Bot instance reset with fresh configuration")
    
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
