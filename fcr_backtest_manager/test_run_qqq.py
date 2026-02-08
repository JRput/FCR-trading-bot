from backtester import run_backtest
from datetime import datetime

start = datetime(2024, 1, 8)
end = datetime(2024, 1, 12)

print(f"Running backtest for QQQ from {start} to {end}...")
results = run_backtest("QQQ", start, end, "A", risk_per_trade=50, initial_capital=1000)

if "error" in results:
    print("Error:", results["error"])
else:
    print("Metrics:", results["metrics"])
    print("First 3 Equity Points:", results["equity_curve"][:3])
    print("Last 3 Equity Points:", results["equity_curve"][-3:])
    print(f"Total Trades: {len(results['trades'])}")
