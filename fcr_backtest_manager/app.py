"""
FCR Backtest Manager - Flask Web Application
Includes TSLA Strategy A Test Bot for Paper Trading
"""

from flask import Flask, render_template, request, jsonify
from datetime import datetime
import pandas as pd

from backtester import run_backtest
from tsla_backtester import run_tsla_backtest
from tsla_live_bot import get_bot, start_bot, stop_bot, get_bot_status

app = Flask(__name__)


@app.route('/')
def index():
    return render_template('index.html')


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


if __name__ == '__main__':
    app.run(debug=False, port=5001)
