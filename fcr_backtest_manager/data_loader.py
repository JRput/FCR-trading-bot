import os
import pandas as pd
import pytz
from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from config import ALPACA_API_KEY, ALPACA_API_SECRET, Timezone

# Initialize Client
client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_API_SECRET)
ET = pytz.timezone(Timezone)

CACHE_DIR = "data_cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

def get_timeframe_obj(minutes):
    """Maps integer minutes to Alpaca TimeFrame."""
    if minutes == 1:
        return TimeFrame.Minute
    return TimeFrame(minutes, TimeFrameUnit.Minute)

def fetch_historical_data(ticker, start_date, end_date, interval_min):
    """
    Fetches historical data for a given ticker and range.
    Uses caching to avoid repeated API calls.
    Returns a DataFrame with index as Eastern Time datetime.
    """
    # Create a cache filename
    filename = f"{ticker}_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}_{interval_min}min.csv"
    cache_path = os.path.join(CACHE_DIR, filename)

    if os.path.exists(cache_path):
        print(f"Loading {ticker} from cache...")
        df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
        # Ensure index is timezone-aware and converted to ET
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert(ET)
        return df

    print(f"Fetching {ticker} from Alpaca API...")

    # Ensure dates are timezone-aware (assume Eastern Time if naive)
    if start_date.tzinfo is None:
        start_date = ET.localize(start_date)
    if end_date.tzinfo is None:
        end_date = ET.localize(end_date)

    start_utc = start_date.astimezone(pytz.utc)
    end_utc = end_date.astimezone(pytz.utc)
    
    timeframe = get_timeframe_obj(interval_min)
    
    request_params = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=timeframe,
        start=start_utc,
        end=end_utc
    )
    
    try:
        bars = client.get_stock_bars(request_params)

        # Handle different response formats from Alpaca SDK
        bar_list = None
        if hasattr(bars, 'data') and bars.data:
            # Dictionary-style access with symbol key
            if ticker in bars.data:
                bar_list = bars.data[ticker]
            elif ticker.upper() in bars.data:
                bar_list = bars.data[ticker.upper()]
            else:
                # Try to get the first (and likely only) key
                keys = list(bars.data.keys())
                if keys:
                    bar_list = bars.data[keys[0]]

        if bar_list is None or len(bar_list) == 0:
            print(f"No data found for {ticker}")
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
        
        # Save to cache before conversion (store as UTC implicitly via CSV)
        df.to_csv(cache_path)

        # Convert to ET for internal use
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert(ET)
        
        return df
        
    except Exception as e:
        print(f"Error fetching data: {e}")
        return pd.DataFrame()

def resample_data(min1_df, interval_min):
    """
    Resamples 1-minute data to a higher timeframe.
    Useful if we want to ensure we have the exact same underlying data.
    """
    if min1_df.empty:
        return pd.DataFrame()
        
    agg_dict = {
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }
    
    # Resample using the interval (use 'min' instead of deprecated 'T')
    resampled = min1_df.resample(f'{interval_min}min').agg(agg_dict).dropna()
    return resampled
