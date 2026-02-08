from data_loader import fetch_historical_data
from datetime import datetime
import pandas as pd

# Test params
ticker = "QQQ" # Different from SPY
start = datetime(2024, 1, 8)
end = datetime(2024, 1, 12)

print(f"Testing data fetch for {ticker}...")
try:
    df = fetch_historical_data(ticker, start, end, 1)
    if df.empty:
        print(f"FAILURE: Data for {ticker} is empty.")
    else:
        print(f"SUCCESS: Fetched {len(df)} rows for {ticker}.")
        print(df.head())
except Exception as e:
    print(f"ERROR: Exception occurred: {e}")
