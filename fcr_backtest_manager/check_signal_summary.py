"""
Summary Report: TSLA Strategy A Signal Check
============================================
Based on bot status and API limitations
"""

import requests
import json
from datetime import datetime
import pytz

ET = pytz.timezone("US/Eastern")

print("=" * 70)
print("  TSLA Strategy A - Signal Analysis Report")
print("=" * 70)
print()

# Get bot status
try:
    status = requests.get("http://127.0.0.1:5001/api/bot/status", timeout=2).json()
    diagnostic = requests.get("http://127.0.0.1:5001/api/bot/diagnostic", timeout=2).json()
    
    print("🤖 BOT STATUS:")
    print(f"   State: {status.get('state', 'unknown')}")
    print(f"   Running: {status.get('running', False)}")
    print(f"   Trades Today: {status.get('trades_today', 0)}")
    print()
    
    # First candle info
    fc = status.get('first_candle', {})
    if fc.get('high'):
        print("📊 FIRST CANDLE (Captured):")
        print(f"   High: ${fc.get('high', 0):.2f}")
        print(f"   Low: ${fc.get('low', 0):.2f}")
        print(f"   Range: ${fc.get('high', 0) - fc.get('low', 0):.2f}")
        print()
    
    # Market status
    market = diagnostic.get('market_status', {})
    print("📈 MARKET STATUS:")
    print(f"   Alpaca says OPEN: {market.get('alpaca_says_open', False)}")
    print(f"   Current Time (ET): {diagnostic.get('current_time_et', 'N/A')}")
    print(f"   Hour: {market.get('hour', 'N/A')}, Minute: {market.get('minute', 'N/A')}")
    print()
    
    # Data fetch status
    data = diagnostic.get('data_fetch', {})
    print("📡 DATA FETCH STATUS:")
    print(f"   Bars Fetched: {data.get('bars_fetched', 0)}")
    if data.get('latest_bar_time'):
        print(f"   Latest Bar: {data.get('latest_bar_time')}")
    else:
        print("   ⚠️ No bars fetched - API limitation detected")
    print()
    
    # Current price
    price = diagnostic.get('current_price', 0)
    if price:
        print(f"💰 Current TSLA Price: ${price:.2f}")
        if fc.get('high'):
            # Check if price has broken first candle
            if price > fc.get('high', 0):
                print(f"   ✅ Price ABOVE first candle high (${fc.get('high', 0):.2f}) - Bullish breakout possible")
            elif price < fc.get('low', 0):
                print(f"   ✅ Price BELOW first candle low (${fc.get('low', 0):.2f}) - Bearish breakdown possible")
            else:
                print(f"   ⚠️ Price within first candle range (${fc.get('low', 0):.2f} - ${fc.get('high', 0):.2f})")
    print()
    
    print("=" * 70)
    print("  ANALYSIS")
    print("=" * 70)
    print()
    
    if status.get('state') == 'scanning_for_signal':
        print("✅ Bot is actively scanning for signals")
        print()
        
        if data.get('bars_fetched', 0) == 0:
            print("❌ ISSUE DETECTED:")
            print("   The bot cannot fetch real-time 1-minute bars due to API limitation:")
            print("   'subscription does not permit querying recent SIP data'")
            print()
            print("   This means:")
            print("   - Bot cannot detect FVGs (needs 1-min bars)")
            print("   - Bot cannot check for retest+engulf signals")
            print("   - Bot cannot validate first candle breaks")
            print()
            print("   SOLUTION:")
            print("   1. Upgrade Alpaca subscription to include real-time data")
            print("   2. Use IEX data feed instead of SIP (if available)")
            print("   3. Wait for delayed data (15-20 min delay)")
            print()
        else:
            print("✅ Bot has access to market data")
            print("   If no signals found, it means:")
            print("   - No FVGs detected yet")
            print("   - FVGs found but no retest+engulf confirmation")
            print("   - Signals found but failed first candle break validation")
            print()
    
    if status.get('active_trade'):
        trade = status.get('active_trade', {})
        print("📊 ACTIVE TRADE:")
        print(f"   Direction: {trade.get('direction')}")
        print(f"   Entry: ${trade.get('entry_price', 0):.2f}")
        print(f"   Stop: ${trade.get('stop_price', 0):.2f}")
        print(f"   Target: ${trade.get('target_price', 0):.2f}")
    else:
        print("📊 No active trade")
    
    print()
    print("=" * 70)

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()

