"""
FCR Backtest Manager - Flask Web Application
Includes TSLA Strategy A Test Bot for Paper Trading
"""

from flask import Flask, render_template, request, jsonify
from datetime import datetime
import pandas as pd

from backtester import run_backtest
from tsla_backtester import run_tsla_backtest
from tsla_live_bot import get_bot, start_bot, stop_bot, get_bot_status, BotState, reset_bot

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0


@app.route('/')
def index():
    response = app.make_response(render_template('index.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/test/html')
def test_html():
    """Test endpoint to verify HTML sections are being served."""
    from flask import render_template_string
    template_content = open('templates/index.html', 'r').read()
    
    # Check if sections exist
    checks = {
        'Detected Signals Card': 'Detected Signals Card' in template_content,
        'Trade History Card': 'Trade History Card' in template_content,
        'signalsTable': 'id="signalsTable"' in template_content,
        'tradeHistoryTable': 'id="tradeHistoryTable"' in template_content,
    }
    
    return jsonify({
        'template_file_exists': True,
        'template_size': len(template_content),
        'sections_found': checks,
        'botPanel_start': template_content.find('id="botPanel"'),
        'signals_position': template_content.find('Detected Signals Card'),
        'trades_position': template_content.find('Trade History Card'),
    })


@app.route('/api/backtest', methods=['POST'])
def api_backtest():
    """Run a backtest with the given parameters."""
    data = request.json
    ticker = data.get('ticker', 'SPY')
    start_date_str = data.get('start_date', '2025-01-12')
    end_date_str = data.get('end_date', '2026-01-15')
    strategy = data.get('strategy', 'A')
    risk_per_trade = float(data.get('risk', 100))
    initial_capital = float(data.get('initial_capital', 1000))
    
    # Custom R:R ratio (optional - uses strategy default if not provided)
    custom_rr_str = data.get('custom_rr')
    custom_rr = float(custom_rr_str) if custom_rr_str else None
    
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400
    
    # Check if using TSLA Trading Strategy
    if strategy == 'TSLA':
        results = run_tsla_backtest(
            start_date=start_date,
            end_date=end_date,
            risk_per_trade=risk_per_trade,
            initial_capital=initial_capital,
            custom_rr=custom_rr,
            use_filters=True,
            use_trailing_be=True
        )
    else:
        results = run_backtest(
            ticker, start_date, end_date, strategy, 
            risk_per_trade, initial_capital, custom_rr
        )
    
    # Process results for JSON serialization
    if "trades" in results:
        for trade in results["trades"]:
            if isinstance(trade.get("entry_time"), (datetime, pd.Timestamp)):
                trade["entry_time"] = trade["entry_time"].strftime('%Y-%m-%d %H:%M:%S')
            if isinstance(trade.get("exit_time"), (datetime, pd.Timestamp)):
                trade["exit_time"] = trade["exit_time"].strftime('%Y-%m-%d %H:%M:%S')
                
    return jsonify(results)


# ═══════════════════════════════════════════════════════════════════════════════
# TSLA STRATEGY A TEST BOT API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/bot/start', methods=['POST'])
def api_bot_start():
    """Start the TSLA Strategy A trading bot."""
    try:
        result = start_bot()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/bot/stop', methods=['POST'])
def api_bot_stop():
    """Stop the TSLA Strategy A trading bot."""
    try:
        result = stop_bot()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/bot/status', methods=['GET'])
def api_bot_status():
    """Get the current status of the TSLA Strategy A trading bot."""
    try:
        status = get_bot_status()
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/bot/account', methods=['GET'])
def api_bot_account():
    """Get the Alpaca paper trading account information."""
    try:
        bot = get_bot()
        account = bot.get_account_info()
        return jsonify(account)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/bot/diagnostic', methods=['GET'])
def api_bot_diagnostic():
    """Run diagnostic checks on bot and Alpaca connection."""
    try:
        from datetime import datetime
        import pytz
        
        bot = get_bot()
        ET = pytz.timezone("US/Eastern")
        now_et = datetime.now(ET)
        
        # Test market status
        clock = bot.trading_client.get_clock()
        market_open = clock.is_open if clock else False
        
        # Calculate if we're in market hours manually
        hour = now_et.hour
        minute = now_et.minute
        weekday = now_et.weekday()
        in_market_hours = (
            weekday < 5 and  # Monday-Friday
            ((hour == 9 and minute >= 30) or (hour >= 10 and hour < 16) or (hour == 16 and minute == 0))
        )
        
        # Test data fetch
        test_bars = bot.fetch_recent_bars(minutes=5)
        
        # Test current price
        current_price = bot.get_current_price()
        
        # Get account
        account = bot.get_account_info()
        
        diagnostic = {
            "current_time_et": now_et.strftime('%Y-%m-%d %H:%M:%S %Z'),
            "current_time_utc": datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S %Z'),
            "market_status": {
                "alpaca_says_open": market_open,
                "calculated_market_hours": in_market_hours,
                "hour": hour,
                "minute": minute,
                "weekday": weekday,
                "clock_time": str(clock.timestamp) if clock else None,
                "next_open": str(clock.next_open) if clock and hasattr(clock, 'next_open') else None,
                "next_close": str(clock.next_close) if clock and hasattr(clock, 'next_close') else None,
            },
            "data_fetch": {
                "bars_fetched": len(test_bars),
                "latest_bar_time": str(test_bars.index[-1]) if not test_bars.empty else None,
                "sample_data": test_bars.tail(3).to_dict('records') if not test_bars.empty else None,
            },
            "current_price": current_price,
            "account": account,
            "bot_state": bot.state.value,
            "first_candle": {
                "captured": bot.first_candle is not None,
                "high": bot.first_candle.high if bot.first_candle else None,
                "low": bot.first_candle.low if bot.first_candle else None,
            },
            "trades_today": bot.trades_today,
        }
        
        return jsonify(diagnostic)
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@app.route('/api/bot/force-market-check', methods=['POST'])
def api_force_market_check():
    """Force a market status check and update bot state if market is open."""
    try:
        bot = get_bot()
        
        # Force market check
        market_open = bot.is_market_open()
        
        # If market is open and bot is waiting, transition to appropriate state
        if market_open and bot.state == BotState.WAITING_MARKET_OPEN:
            bot.state = BotState.WAITING_FIRST_CANDLE
            return jsonify({
                "status": "updated",
                "message": "Market detected as open, bot state updated",
                "new_state": bot.state.value
            })
        
        return jsonify({
            "status": "checked",
            "market_open": market_open,
            "current_state": bot.state.value
        })
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@app.route('/api/bot/signals', methods=['GET'])
def api_bot_signals():
    """Get detected signals for the current session."""
    try:
        bot = get_bot()
        return jsonify(bot.detected_signals)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/bot/trades', methods=['GET'])
def api_bot_trades():
    """Get trade history for the current session."""
    try:
        bot = get_bot()
        return jsonify(bot.trade_history)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/bot/reset', methods=['POST'])
def api_bot_reset():
    """Reset the bot with fresh configuration (reloads config.py)."""
    try:
        bot = reset_bot()
        account = bot.get_account_info()
        return jsonify({
            "status": "reset",
            "message": "Bot reset with fresh configuration",
            "account": account,
            "config": {
                "risk_per_trade": bot.config["risk_per_trade"],
                "capital": bot.config["capital"],
                "rr": bot.rr,
            }
        })
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5001))
    app.run(debug=False, port=port)
