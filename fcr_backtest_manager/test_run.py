from backtester import run_backtest
from datetime import datetime

start = datetime(2024, 1, 8) # A Monday
end = datetime(2024, 1, 12) # A Friday

print(f"Running test backtest from {start} to {end}...")
results = run_backtest("SPY", start, end, "A")
print("Results Keys:", results.keys())
if "metrics" in results:
    print("Metrics:", results["metrics"])
else:
    print("Error:", results.get("error"))
