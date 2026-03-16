"""
Live Signal Checker for TSLA Strategy A
========================================
This script checks if there should be a trading signal in the current live market
without interfering with the running bot.
"""

import sys
from datetime import datetime, timedelta
import pandas as pd
import pytz

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from config import ALPACA_API_KEY, ALPACA_API_SECRET, Timezone
from strategy import detect_fvgs, check_retest_and_engulf, filter_by_first_candle_break

ET = pytz.timezone(Timezone)
TICKER = "TSLA"

def get_current_price():
    """Get current TSLA price."""
    try:
        client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_API_SECRET)
        request = StockLatestQuoteRequest(symbol_or_symbols=TICKER)
        quote = client.get_stock_latest_quote(request)
        if TICKER in quote:
            return float(quote[TICKER].ask_price)
        return None
    except Exception as e:
        print(f"Error getting price: {e}")
        return None

def fetch_bars(timeframe_minutes, start_time, end_time):
    """Fetch bars for a given timeframe."""
    try:
        client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_API_SECRET)
        
        if timeframe_minutes == 1:
            timeframe = TimeFrame.Minute
        else:
            timeframe = TimeFrame(timeframe_minutes, TimeFrameUnit.Minute)
        
        # Ensure times are timezone-aware
        if start_time.tzinfo is None:
            start_time = ET.localize(start_time)
        if end_time.tzinfo is None:
            end_time = ET.localize(end_time)
        
        # Convert to UTC for API
        start_utc = start_time.astimezone(pytz.utc)
        end_utc = end_time.astimezone(pytz.utc)
        
        request = StockBarsRequest(
            symbol_or_symbols=TICKER,
            timeframe=timeframe,
            start=start_utc,
            end=end_utc
        )
        
        bars = client.get_stock_bars(request)
        
        # Handle different response formats
        bar_list = None
        if hasattr(bars, 'data') and bars.data:
            if TICKER in bars.data:
                bar_list = bars.data[TICKER]
            elif TICKER.upper() in bars.data:
                bar_list = bars.data[TICKER.upper()]
            else:
                keys = list(bars.data.keys())
                if keys:
                    bar_list = bars.data[keys[0]]
        
        if bar_list is None or len(bar_list) == 0:
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
        
        return df
    except Exception as e:
        error_msg = str(e)
        if "subscription does not permit" in error_msg.lower() or "403" in error_msg:
            print(f"⚠️ API Limitation: Paper trading account may not have real-time data access")
            print(f"   Error: {error_msg}")
            print(f"   This is a limitation of the Alpaca paper trading data subscription.")
            print(f"   The bot may be experiencing the same limitation.")
        else:
            print(f"Error fetching bars: {e}")
            import traceback
            traceback.print_exc()
        return pd.DataFrame()

def get_first_candle_today():
    """Get the first 5-minute candle of today (9:30 AM)."""
    now = datetime.now(ET)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    market_open = today.replace(hour=9, minute=30)
    candle_close = market_open + timedelta(minutes=5)
    
    # Fetch 5-min bars for today
    start_time = market_open - timedelta(minutes=1)
    end_time = now  # Up to current time
    
    df = fetch_bars(5, start_time, end_time)
    
    if df.empty:
        print("⚠️ No 5-minute bars found for today")
        return None
    
    # Find the 9:30 candle
    candidates = df.between_time('09:30', '09:30')
    
    if candidates.empty:
        print("⚠️ No 9:30 AM candle found")
        return None
    
    first_candle = candidates.iloc[0]
    
    return {
        'high': float(first_candle['High']),
        'low': float(first_candle['Low']),
        'open': float(first_candle['Open']),
        'close': float(first_candle['Close']),
        'time': first_candle.name,
        'close_time': first_candle.name + timedelta(minutes=5)
    }

def check_for_signals():
    """Check if there should be a Strategy A signal."""
    print("=" * 70)
    print("  TSLA Strategy A - Live Signal Check")
    print("=" * 70)
    print()
    
    now = datetime.now(ET)
    print(f"Current Time (ET): {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print()
    
    # Check bot status first
    try:
        import requests
        bot_status = requests.get("http://127.0.0.1:5001/api/bot/status", timeout=2).json()
        print("🤖 Bot Status:")
        print(f"   State: {bot_status.get('state', 'unknown')}")
        print(f"   Running: {bot_status.get('running', False)}")
        if bot_status.get('first_candle'):
            fc = bot_status['first_candle']
            print(f"   First Candle: High=${fc.get('high', 'N/A')}, Low=${fc.get('low', 'N/A')}")
        print()
    except:
        print("⚠️ Could not get bot status (bot may not be running)")
        print()
    
    # Get current price
    current_price = get_current_price()
    if current_price:
        print(f"✅ Current TSLA Price: ${current_price:.2f}")
    else:
        print("❌ Could not get current price")
        return
    print()
    
    # Get first candle
    print("📊 Checking First Candle (5-min at 9:30 AM)...")
    first_candle = get_first_candle_today()
    
    if not first_candle:
        print("❌ Could not get first candle - market may not have opened yet")
        return
    
    print(f"✅ First Candle Captured:")
    print(f"   Time: {first_candle['time'].strftime('%H:%M:%S')}")
    print(f"   High: ${first_candle['high']:.2f}")
    print(f"   Low: ${first_candle['low']:.2f}")
    print(f"   Open: ${first_candle['open']:.2f}")
    print(f"   Close: ${first_candle['close']:.2f}")
    print(f"   Range: ${first_candle['high'] - first_candle['low']:.2f}")
    print()
    
    # Fetch 1-minute bars for today (for FVG detection)
    print("📊 Fetching 1-minute bars for signal detection...")
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    df_1m = fetch_bars(1, today_start, now)
    
    if df_1m.empty:
        print("❌ No 1-minute bars found")
        return
    
    print(f"✅ Fetched {len(df_1m)} 1-minute bars")
    print(f"   Range: {df_1m.index[0].strftime('%H:%M:%S')} to {df_1m.index[-1].strftime('%H:%M:%S')}")
    print()
    
    # Detect FVGs
    print("🔍 Detecting Fair Value Gaps...")
    fvgs = detect_fvgs(df_1m)
    print(f"✅ Found {len(fvgs)} FVGs")
    
    if len(fvgs) == 0:
        print("❌ No FVGs detected - no signal possible")
        return
    
    # Show FVGs
    for i, fvg in enumerate(fvgs):
        print(f"   FVG {i+1}: {fvg['type'].upper()} at {fvg['time'].strftime('%H:%M:%S')}")
        print(f"      Gap: ${fvg['gap_bottom']:.2f} - ${fvg['gap_top']:.2f}")
    print()
    
    # Check for retest and engulf
    print("🔍 Checking for Retest + Engulf signals...")
    signals_found = []
    
    for i, fvg in enumerate(fvgs):
        signal = check_retest_and_engulf(df_1m, fvg)
        if signal:
            print(f"✅ Signal found from FVG {i+1}:")
            print(f"   Direction: {signal['direction']}")
            print(f"   Entry Price: ${signal['entry_price']:.2f}")
            print(f"   Stop Price: ${signal['stop_price']:.2f}")
            print(f"   Entry Time: {signal['entry_time'].strftime('%H:%M:%S')}")
            print(f"   Risk: ${signal['risk']:.2f}")
            
            # Check first candle break validation
            print(f"   Checking First Candle Break validation...")
            fc_close_time = pd.Timestamp(first_candle['close_time'])
            
            if filter_by_first_candle_break(
                df_1m,
                first_candle['high'],
                first_candle['low'],
                signal,
                strategy_type='A',
                first_candle_close_time=fc_close_time
            ):
                print(f"   ✅ PASSED First Candle Break validation!")
                signals_found.append(signal)
            else:
                print(f"   ❌ FAILED First Candle Break validation")
            print()
    
    # Summary
    print("=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    
    if len(signals_found) > 0:
        print(f"✅ {len(signals_found)} VALID SIGNAL(S) FOUND:")
        for i, signal in enumerate(signals_found):
            print(f"\n   Signal {i+1}:")
            print(f"   - Direction: {signal['direction']}")
            print(f"   - Entry: ${signal['entry_price']:.2f}")
            print(f"   - Stop: ${signal['stop_price']:.2f}")
            print(f"   - Entry Time: {signal['entry_time'].strftime('%H:%M:%S')}")
            risk = signal['risk']
            target = signal['entry_price'] + (risk * 5.5) if signal['direction'] == 'LONG' else signal['entry_price'] - (risk * 5.5)
            print(f"   - Target: ${target:.2f} (5.5R)")
    else:
        print("❌ NO VALID SIGNALS FOUND")
        print("\n   Possible reasons:")
        print("   - No FVGs detected")
        print("   - FVGs found but no retest+engulf confirmation")
        print("   - Signals found but failed first candle break validation")
        print("   - Market hasn't opened yet (need 9:30 AM first candle)")
    
    print()
    print("=" * 70)

if __name__ == "__main__":
    try:
        check_for_signals()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

