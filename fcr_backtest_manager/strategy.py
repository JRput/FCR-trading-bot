"""
FCR Trading Strategy - Core logic for First Candle Rule with FVG confirmation.
Supports both Strategy A (Breakout) and Strategy B (Liquidity Grab Reversal).
"""

import pandas as pd
from datetime import datetime, time
from typing import Dict, List, Optional, Any

# Strategy Definitions
# Default R:R ratios - can be overridden in backtester
STRAT_A = {"label": "Strategy A (5min->1min)", "anchor_min": 5, "confirm_min": 1, "rr": 5.5}  # Optimized from 3.0
STRAT_B = {"label": "Strategy B (30min->5min)", "anchor_min": 30, "confirm_min": 5, "rr": 2}


def get_strategy_config(strategy_type: str, custom_rr: Optional[float] = None) -> Dict[str, Any]:
    """
    Get strategy configuration with optional custom R:R ratio.
    
    Args:
        strategy_type: 'A' or 'B'
        custom_rr: Optional custom risk/reward ratio to override default
        
    Returns:
        Strategy configuration dictionary
    """
    if strategy_type == 'A':
        config = STRAT_A.copy()
    elif strategy_type == 'B':
        config = STRAT_B.copy()
    else:
        config = STRAT_A.copy()
    
    # Override R:R if custom value provided
    if custom_rr is not None:
        config['rr'] = custom_rr
        config['label'] = f"{config['label']}, {custom_rr}:1"
    else:
        config['label'] = f"{config['label']}, {config['rr']}:1"
    
    return config


def get_first_candle(df: pd.DataFrame, anchor_timeframe_min: int) -> Dict[Any, Dict[str, Any]]:
    """
    Extracts the first candle of the day (9:30 AM ET).
    Assumes df index is datetime (ET).
    
    Returns: Dictionary of {date: {high, low, close, open, time}}
    """
    first_candles = {}
    
    # Group by date
    grouped = df.groupby(df.index.date)
    
    for date, day_df in grouped:
        # Find the 9:30 candle
        candidates = day_df.between_time('09:30', '09:30')
        
        if not candidates.empty:
            first = candidates.iloc[0]
            first_candles[date] = {
                'high': float(first['High']),
                'low': float(first['Low']),
                'close': float(first['Close']),
                'open': float(first['Open']),
                'time': first.name
            }
            
    return first_candles


def detect_fvgs(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Scans for Fair Value Gaps.
    
    Returns: List of FVG dictionaries
    """
    fvgs = []
    if len(df) < 3:
        return fvgs
        
    highs = df['High'].values
    lows = df['Low'].values
    times = df.index
    
    for i in range(len(df) - 2):
        # Bullish FVG: candle[i+2].low > candle[i].high
        if lows[i+2] > highs[i]:
            fvgs.append({
                "type": "bullish",
                "time": times[i+1],  # The time of the middle candle
                "gap_top": float(lows[i+2]),
                "gap_bottom": float(highs[i]),
                "candle_idx": i+1
            })
            
        # Bearish FVG: candle[i+2].high < candle[i].low
        if highs[i+2] < lows[i]:
            fvgs.append({
                "type": "bearish",
                "time": times[i+1],
                "gap_top": float(lows[i]),
                "gap_bottom": float(highs[i+2]),
                "candle_idx": i+1
            })
            
    return fvgs


def check_retest_and_engulf(df: pd.DataFrame, fvg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Looks for retest and engulfing confirmation after the FVG.
    
    Returns: Signal dict if confirmed, else None
    """
    idx = fvg["candle_idx"]
    gap_top = fvg["gap_top"]
    gap_bottom = fvg["gap_bottom"]
    fvg_type = fvg["type"]
    
    # Start searching from 2 candles after the FVG middle candle
    start_idx = idx + 2
    
    if start_idx >= len(df) - 1:
        return None
        
    closes = df["Close"].values
    opens = df["Open"].values
    highs = df["High"].values
    lows = df["Low"].values
    times = df.index
    
    for j in range(start_idx, len(df) - 1):
        retest_idx = j
        engulf_idx = j + 1
        
        # Bullish: Retest pulls back into gap, then engulfs up
        if fvg_type == "bullish":
            # Retest: Low is within gap
            retest_hit = gap_bottom <= lows[retest_idx] <= gap_top
            
            # Engulf: Close is higher than previous high
            engulf_hit = closes[engulf_idx] > highs[retest_idx]
            
            if retest_hit and engulf_hit:
                entry = closes[engulf_idx]
                stop = lows[retest_idx]
                
                # Validate stop makes sense
                if entry <= stop:
                    continue
                    
                return {
                    "direction": "LONG",
                    "entry_price": float(entry),
                    "stop_price": float(stop),
                    "risk": float(entry - stop),
                    "retest_time": times[retest_idx],
                    "entry_time": times[engulf_idx],
                    "fvg": fvg
                }
                
        # Bearish: Retest pulls back into gap, then engulfs down
        elif fvg_type == "bearish":
            # Retest: High is within gap
            retest_hit = gap_bottom <= highs[retest_idx] <= gap_top
            
            # Engulf: Close is lower than previous low
            engulf_hit = closes[engulf_idx] < lows[retest_idx]
            
            if retest_hit and engulf_hit:
                entry = closes[engulf_idx]
                stop = highs[retest_idx]
                
                # Validate stop makes sense
                if entry >= stop:
                    continue
                    
                return {
                    "direction": "SHORT",
                    "entry_price": float(entry),
                    "stop_price": float(stop),
                    "risk": float(stop - entry),
                    "retest_time": times[retest_idx],
                    "entry_time": times[engulf_idx],
                    "fvg": fvg
                }
                
    return None


def filter_by_first_candle_break(
    df: pd.DataFrame, 
    first_high: float, 
    first_low: float, 
    signal: Dict[str, Any], 
    strategy_type: str = 'A', 
    first_candle_close_time: Optional[pd.Timestamp] = None
) -> bool:
    """
    Validates signal based on first candle range interaction.
    
    Strategy A (Breakout Continuation):
        - Bullish FVG: price must have broken ABOVE first_high before/during FVG
        - Bearish FVG: price must have broken BELOW first_low before/during FVG
        
    Strategy B (Liquidity Grab Reversal):
        - Bullish FVG: price must have traded INTO the LOW (liquidity grab), then FVG moves back up
        - Bearish FVG: price must have traded INTO the HIGH (liquidity grab), then FVG moves back down
    """
    fvg_time = signal["fvg"]["time"]
    fvg_type = signal["fvg"]["type"]
    
    # Ensure FVG forms AFTER the first candle closes
    if first_candle_close_time is not None:
        if fvg_time <= first_candle_close_time:
            return False
    
    # Include candles up to and including the FVG formation
    pre_and_during_fvg = df[df.index <= fvg_time]
    
    if pre_and_during_fvg.empty:
        return False
    
    if strategy_type == 'A':
        # Strategy A: Breakout Continuation
        if fvg_type == "bullish":
            if pre_and_during_fvg['High'].max() > first_high:
                return True
        elif fvg_type == "bearish":
            if pre_and_during_fvg['Low'].min() < first_low:
                return True
                
    elif strategy_type == 'B':
        # Strategy B: Liquidity Grab Reversal
        if fvg_type == "bullish":
            # Price should have touched/gone below first_low
            if pre_and_during_fvg['Low'].min() <= first_low:
                return True
        elif fvg_type == "bearish":
            # Price should have touched/gone above first_high
            if pre_and_during_fvg['High'].max() >= first_high:
                return True
                
    return False


def get_strategy_label(strategy_type: str) -> str:
    """Get a human-readable label for the strategy."""
    if strategy_type == 'A':
        return STRAT_A['label']
    elif strategy_type == 'B':
        return STRAT_B['label']
    return f"Strategy {strategy_type}"
