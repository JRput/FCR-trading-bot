"""
TSLA Trading Strategy - Optimized FCR Strategy for TSLA

Based on detailed analysis of 2,254 trades across 2019-2025:
- Current Strategy A win rate: ~18-20%
- Target win rate: 40-50%
- Keeping R:R at 5.5:1

Key Improvements:
1. Entry time filter: Only trade during optimal hours (9:30-11:00, avoid 12:00-15:00)
2. First candle range filter: 0.5-1.5% range performs best
3. First candle body ratio filter: Prefer strong candles (>40% body)
4. Trailing stop to break-even after 1R profit (reduces losing trades that went in profit first)
5. FVG size filter: Minimum FVG size for better signal quality
6. First candle alignment confirmation for higher probability setups
"""

import pandas as pd
import numpy as np
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Any

# TSLA Strategy Configuration
TSLA_STRATEGY = {
    "label": "TSLA Trading Strategy (Optimized FCR)",
    "anchor_min": 5,          # First candle timeframe
    "confirm_min": 1,         # FVG detection timeframe  
    "rr": 5.5,               # Risk:Reward ratio
    "risk_per_trade": 1000,  # Fixed risk per trade
    "initial_capital": 5000, # Starting capital
    
    # Entry Time Filters
    "min_entry_hour": 9,      # Earliest entry hour (9:30)
    "max_entry_hour": 11,     # Latest entry hour (avoid 12:00+)
    "max_entry_minute": 30,   # Latest minute in max hour
    
    # First Candle Filters
    "min_fc_range_pct": 0.5,  # Minimum first candle range %
    "max_fc_range_pct": 2.0,  # Maximum first candle range %
    "min_fc_body_ratio": 0.3, # Minimum body/range ratio (30%)
    
    # FVG Filters
    "min_fvg_size_pct": 0.05, # Minimum FVG size as % of price
    "max_fvg_size_pct": 1.0,  # Maximum FVG size (avoid extreme gaps)
    
    # Trade Management
    "use_trailing_be": True,  # Move stop to break-even after 1R
    "be_trigger_r": 1.0,      # R-multiple to trigger break-even
    "require_fc_alignment": False,  # Don't require alignment (counter works too)
    
    # Risk Management
    "max_trades_per_day": 1,  # Max trades per day
}


def get_tsla_strategy_config(custom_rr: Optional[float] = None) -> Dict[str, Any]:
    """Get TSLA strategy configuration with optional custom R:R."""
    config = TSLA_STRATEGY.copy()
    
    if custom_rr is not None:
        config['rr'] = custom_rr
        
    return config


def validate_entry_time(entry_time: pd.Timestamp, config: Dict[str, Any]) -> bool:
    """
    Check if entry time falls within optimal trading window.
    Returns True if entry time is valid.
    """
    hour = entry_time.hour
    minute = entry_time.minute
    
    min_hour = config.get('min_entry_hour', 9)
    max_hour = config.get('max_entry_hour', 11)
    max_minute = config.get('max_entry_minute', 30)
    
    # Before minimum hour
    if hour < min_hour:
        return False
    
    # After maximum hour + minute
    if hour > max_hour:
        return False
    
    if hour == max_hour and minute > max_minute:
        return False
    
    return True


def validate_first_candle(first_candle_data: Dict[str, Any], current_price: float, config: Dict[str, Any]) -> bool:
    """
    Validate first candle characteristics.
    Returns True if first candle meets quality criteria.
    """
    fc_high = first_candle_data['high']
    fc_low = first_candle_data['low']
    fc_open = first_candle_data['open']
    fc_close = first_candle_data['close']
    
    # Calculate range as percentage
    fc_range = fc_high - fc_low
    fc_range_pct = (fc_range / fc_low) * 100
    
    # Calculate body ratio
    body = abs(fc_close - fc_open)
    body_ratio = body / fc_range if fc_range > 0 else 0
    
    # Check range limits
    min_range = config.get('min_fc_range_pct', 0.5)
    max_range = config.get('max_fc_range_pct', 2.0)
    
    if fc_range_pct < min_range or fc_range_pct > max_range:
        return False
    
    # Check body ratio
    min_body_ratio = config.get('min_fc_body_ratio', 0.3)
    if body_ratio < min_body_ratio:
        return False
    
    return True


def validate_fvg_quality(fvg: Dict[str, Any], current_price: float, config: Dict[str, Any]) -> bool:
    """
    Validate FVG size and quality.
    Returns True if FVG meets criteria.
    """
    gap_top = fvg['gap_top']
    gap_bottom = fvg['gap_bottom']
    gap_size = gap_top - gap_bottom
    
    # Calculate gap as percentage of price
    gap_pct = (gap_size / current_price) * 100
    
    min_fvg = config.get('min_fvg_size_pct', 0.05)
    max_fvg = config.get('max_fvg_size_pct', 1.0)
    
    if gap_pct < min_fvg or gap_pct > max_fvg:
        return False
    
    return True


def check_first_candle_alignment(
    direction: str, 
    first_candle_data: Dict[str, Any], 
    require_alignment: bool = False
) -> bool:
    """
    Check if trade direction aligns with first candle direction.
    
    Note: Analysis shows counter-trades also work well for TSLA,
    so this is optional and disabled by default.
    """
    if not require_alignment:
        return True
        
    fc_bullish = first_candle_data['close'] > first_candle_data['open']
    
    if direction == "LONG" and fc_bullish:
        return True
    if direction == "SHORT" and not fc_bullish:
        return True
    
    return False


def simulate_trade_with_trailing(
    entry_price: float,
    stop_price: float,
    target_price: float,
    direction: str,
    future_prices: pd.DataFrame,
    risk_amount: float,
    rr: float,
    use_trailing_be: bool = True,
    be_trigger_r: float = 1.0
) -> Dict[str, Any]:
    """
    Simulate trade execution with optional trailing stop to break-even.
    
    Key improvement: After reaching 1R profit, move stop to break-even.
    This converts many would-be losses into break-even or small wins.
    """
    risk_dist = abs(entry_price - stop_price)
    current_stop = stop_price
    be_price = entry_price  # Break-even price
    stop_moved_to_be = False
    
    # Track max favorable excursion
    max_favorable = 0
    
    for ts, row in future_prices.iterrows():
        current_high = row['High']
        current_low = row['Low']
        
        if direction == "LONG":
            # Calculate current favorable R-multiple
            favorable_r = (current_high - entry_price) / risk_dist if risk_dist > 0 else 0
            max_favorable = max(max_favorable, favorable_r)
            
            # Move stop to break-even after reaching trigger
            if use_trailing_be and favorable_r >= be_trigger_r and not stop_moved_to_be:
                current_stop = entry_price  # Move stop to break-even
                stop_moved_to_be = True
            
            # Check stop hit (use current stop which may have moved to BE)
            if current_low <= current_stop:
                exit_price = current_stop
                if stop_moved_to_be:
                    # Break-even or small win/loss around entry
                    pnl = 0  # Simplified: break-even
                    outcome = "BE"
                else:
                    pnl = -risk_amount
                    outcome = "LOSS"
                return {
                    "outcome": outcome,
                    "exit_price": exit_price,
                    "exit_time": ts,
                    "pnl": pnl,
                    "max_favorable_r": max_favorable,
                    "stop_moved_to_be": stop_moved_to_be
                }
            
            # Check target hit
            if current_high >= target_price:
                return {
                    "outcome": "WIN",
                    "exit_price": target_price,
                    "exit_time": ts,
                    "pnl": risk_amount * rr,
                    "max_favorable_r": max_favorable,
                    "stop_moved_to_be": stop_moved_to_be
                }
                
        elif direction == "SHORT":
            # Calculate current favorable R-multiple
            favorable_r = (entry_price - current_low) / risk_dist if risk_dist > 0 else 0
            max_favorable = max(max_favorable, favorable_r)
            
            # Move stop to break-even after reaching trigger
            if use_trailing_be and favorable_r >= be_trigger_r and not stop_moved_to_be:
                current_stop = entry_price  # Move stop to break-even
                stop_moved_to_be = True
            
            # Check stop hit
            if current_high >= current_stop:
                exit_price = current_stop
                if stop_moved_to_be:
                    pnl = 0
                    outcome = "BE"
                else:
                    pnl = -risk_amount
                    outcome = "LOSS"
                return {
                    "outcome": outcome,
                    "exit_price": exit_price,
                    "exit_time": ts,
                    "pnl": pnl,
                    "max_favorable_r": max_favorable,
                    "stop_moved_to_be": stop_moved_to_be
                }
            
            # Check target hit
            if current_low <= target_price:
                return {
                    "outcome": "WIN",
                    "exit_price": target_price,
                    "exit_time": ts,
                    "pnl": risk_amount * rr,
                    "max_favorable_r": max_favorable,
                    "stop_moved_to_be": stop_moved_to_be
                }
    
    # Timeout - trade didn't complete
    if not future_prices.empty:
        last_row = future_prices.iloc[-1]
        last_price = last_row['Close']
        
        if direction == "LONG":
            pnl = ((last_price - entry_price) / risk_dist * risk_amount) if risk_dist > 0 else 0
        else:
            pnl = ((entry_price - last_price) / risk_dist * risk_amount) if risk_dist > 0 else 0
            
        return {
            "outcome": "TIMEOUT",
            "exit_price": last_price,
            "exit_time": future_prices.index[-1],
            "pnl": pnl,
            "max_favorable_r": max_favorable,
            "stop_moved_to_be": stop_moved_to_be
        }
    
    return {
        "outcome": "NO_DATA",
        "exit_price": None,
        "exit_time": None,
        "pnl": 0,
        "max_favorable_r": 0,
        "stop_moved_to_be": False
    }


def detect_fvgs_tsla(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Scans for Fair Value Gaps with enhanced tracking.
    Same logic as original but with additional metadata.
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
                "time": times[i+1],
                "gap_top": float(lows[i+2]),
                "gap_bottom": float(highs[i]),
                "gap_size": float(lows[i+2] - highs[i]),
                "candle_idx": i+1
            })
            
        # Bearish FVG: candle[i+2].high < candle[i].low
        if highs[i+2] < lows[i]:
            fvgs.append({
                "type": "bearish",
                "time": times[i+1],
                "gap_top": float(lows[i]),
                "gap_bottom": float(highs[i+2]),
                "gap_size": float(lows[i] - highs[i+2]),
                "candle_idx": i+1
            })
            
    return fvgs


def check_retest_and_engulf_tsla(df: pd.DataFrame, fvg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Enhanced retest and engulfing confirmation.
    Same logic as original with additional metadata.
    """
    idx = fvg["candle_idx"]
    gap_top = fvg["gap_top"]
    gap_bottom = fvg["gap_bottom"]
    fvg_type = fvg["type"]
    
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
        
        if fvg_type == "bullish":
            retest_hit = gap_bottom <= lows[retest_idx] <= gap_top
            engulf_hit = closes[engulf_idx] > highs[retest_idx]
            
            if retest_hit and engulf_hit:
                entry = closes[engulf_idx]
                stop = lows[retest_idx]
                
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
                
        elif fvg_type == "bearish":
            retest_hit = gap_bottom <= highs[retest_idx] <= gap_top
            engulf_hit = closes[engulf_idx] < lows[retest_idx]
            
            if retest_hit and engulf_hit:
                entry = closes[engulf_idx]
                stop = highs[retest_idx]
                
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


def filter_by_first_candle_break_tsla(
    df: pd.DataFrame, 
    first_high: float, 
    first_low: float, 
    signal: Dict[str, Any], 
    first_candle_close_time: Optional[pd.Timestamp] = None
) -> bool:
    """
    Strategy A breakout confirmation for TSLA.
    Price must break first candle high/low before/during FVG formation.
    """
    fvg_time = signal["fvg"]["time"]
    fvg_type = signal["fvg"]["type"]
    
    if first_candle_close_time is not None:
        if fvg_time <= first_candle_close_time:
            return False
    
    pre_and_during_fvg = df[df.index <= fvg_time]
    
    if pre_and_during_fvg.empty:
        return False
    
    if fvg_type == "bullish":
        if pre_and_during_fvg['High'].max() > first_high:
            return True
    elif fvg_type == "bearish":
        if pre_and_during_fvg['Low'].min() < first_low:
            return True
            
    return False


def get_strategy_label_tsla() -> str:
    """Get human-readable label for TSLA strategy."""
    return TSLA_STRATEGY['label']

