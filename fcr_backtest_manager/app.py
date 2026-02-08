"""
FCR Backtest Manager - Flask Web Application
"""

from flask import Flask, render_template, request, jsonify
from datetime import datetime
import pandas as pd

from backtester import run_backtest

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


if __name__ == '__main__':
    app.run(debug=False, port=5001)
