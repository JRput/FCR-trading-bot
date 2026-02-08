"""
FCR Backtester - Simulates trading strategy on historical data.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from data_loader import fetch_historical_data, resample_data
from strategy import (
    get_strategy_config, get_first_candle, detect_fvgs, 
    check_retest_and_engulf, filter_by_first_candle_break,
    get_strategy_label
)


def run_backtest(
    ticker: str,
    start_date: datetime,
    end_date: datetime,
    strategy_type: str = 'A',
    risk_per_trade: float = 100.0,
    initial_capital: float = 1000.0,
    custom_rr: Optional[float] = None
) -> Dict[str, Any]:
    """
    Runs the backtest for the given parameters.
    
    Parameters:
    - ticker: Stock symbol
    - start_date: Backtest start date
    - end_date: Backtest end date
    - strategy_type: 'A' or 'B'
    - risk_per_trade: Dollar amount risked per trade
    - initial_capital: Starting capital
    - custom_rr: Optional custom risk/reward ratio (overrides strategy default)
    
    Returns: Dictionary with metrics, equity_curve, and trades
    """
    # 1. Get Strategy Config (with optional custom R:R)
    strat_config = get_strategy_config(strategy_type, custom_rr)
    
    anchor_min = strat_config['anchor_min']
    confirm_min = strat_config['confirm_min']
    rr = strat_config['rr']
    
    # 2. Fetch Data
    print(f"Fetching data for {ticker} from {start_date.date()} to {end_date.date()}...")
    df_1m = fetch_historical_data(ticker, start_date, end_date, 1)
    
    if df_1m.empty:
        return {
            "error": f"No data found for {ticker}. Please check the ticker symbol and date range.",
            "metrics": {
                "total_trades": 0,
                "win_rate": 0,
                "total_pnl": 0,
                "profit_factor": 0,
                "final_equity": initial_capital,
                "initial_capital": initial_capital
            },
            "equity_curve": [{"date": start_date.strftime('%Y-%m-%d'), "equity": initial_capital}],
            "trades": []
        }
        
    # Resample to Anchor and Confirmation timeframes
    df_anchor = resample_data(df_1m, anchor_min)
    df_confirm = resample_data(df_1m, confirm_min)
    
    # 3. Iterate by Day
    trades = []
    equity_curve = [{"date": start_date.strftime('%Y-%m-%d'), "equity": initial_capital}]
    current_equity = initial_capital
    
    days = df_1m.index.normalize().unique()
    days = sorted(days)

    for day in days:
        day_str = day.strftime('%Y-%m-%d')
        
        # Check bankruptcy
        if current_equity <= 0:
            equity_curve.append({"date": day_str, "equity": 0})
            continue

        # Slice data for the day
        daily_anchor = df_anchor[df_anchor.index.normalize() == day]
        daily_confirm = df_confirm[df_confirm.index.normalize() == day]
        daily_1m = df_1m[df_1m.index.normalize() == day]
        
        if daily_anchor.empty or daily_confirm.empty:
            equity_curve.append({"date": day_str, "equity": current_equity})
            continue
            
        # A. Get First Candle (Anchor)
        candidates = daily_anchor.between_time('09:30', '09:30')
        if candidates.empty:
            equity_curve.append({"date": day_str, "equity": current_equity})
            continue
            
        first_candle = candidates.iloc[0]
        first_high = float(first_candle['High'])
        first_low = float(first_candle['Low'])
        first_close = float(first_candle['Close'])
        
        # Calculate when the first candle closes
        first_candle_close_time = first_candle.name + pd.Timedelta(minutes=anchor_min)
        
        # B. Detect FVGs
        fvgs = detect_fvgs(daily_confirm)
        
        daily_pnl = 0
        trades_today = 0
        
        for fvg in fvgs:
            # Only take 1 trade per day
            if trades_today >= 1:
                break
                
            signal = check_retest_and_engulf(daily_confirm, fvg)
            
            if signal:
                if filter_by_first_candle_break(
                    daily_confirm, first_high, first_low, signal, 
                    strategy_type=strategy_type, 
                    first_candle_close_time=first_candle_close_time
                ):
                    # Trade execution logic
                    entry_price = signal['entry_price']
                    stop_price = signal['stop_price']
                    direction = signal['direction']
                    entry_time = signal['entry_time']
                    
                    risk_dist = abs(entry_price - stop_price)
                    if risk_dist == 0:
                        continue

                    target_price = (entry_price + (risk_dist * rr) if direction == "LONG" 
                                    else entry_price - (risk_dist * rr))
                    
                    # Position Sizing: Risk a fixed amount (risk_per_trade)
                    # But don't risk more than we have
                    actual_risk = min(risk_per_trade, current_equity)
                    
                    # PnL simulation
                    future_prices = daily_1m[daily_1m.index > entry_time]
                    outcome = "OPEN"
                    pnl = 0
                    exit_time = None
                    exit_price = None
                    
                    for ts, row in future_prices.iterrows():
                        current_high = row['High']
                        current_low = row['Low']
                        
                        if direction == "LONG":
                            if current_low <= stop_price:
                                outcome = "LOSS"
                                exit_price = stop_price
                                exit_time = ts
                                pnl = -actual_risk
                                break
                            if current_high >= target_price:
                                outcome = "WIN"
                                exit_price = target_price
                                exit_time = ts
                                pnl = actual_risk * rr
                                break
                        elif direction == "SHORT":
                            if current_high >= stop_price:
                                outcome = "LOSS"
                                exit_price = stop_price
                                exit_time = ts
                                pnl = -actual_risk
                                break
                            if current_low <= target_price:
                                outcome = "WIN"
                                exit_price = target_price
                                exit_time = ts
                                pnl = actual_risk * rr
                                break
                    
                    if outcome == "OPEN" and not future_prices.empty:
                        last_row = future_prices.iloc[-1]
                        last_price = last_row['Close']
                        exit_time = future_prices.index[-1]
                        exit_price = last_price
                        pnl = ((last_price - entry_price) / risk_dist * actual_risk 
                               if direction == "LONG" 
                               else (entry_price - last_price) / risk_dist * actual_risk)
                        outcome = "TIMEOUT"
                        
                    trades.append({
                        "entry_time": entry_time,
                        "exit_time": exit_time,
                        "ticker": ticker,
                        "direction": direction,
                        "entry_price": round(entry_price, 4),
                        "exit_price": round(exit_price, 4) if exit_price else None,
                        "stop_price": round(stop_price, 4),
                        "target_price": round(target_price, 4),
                        "outcome": outcome,
                        "pnl": round(pnl, 2),
                        "rr": rr,
                        "balance_after": round(current_equity + pnl, 2),
                        "fvg_type": fvg['type']
                    })
                    
                    current_equity += pnl
                    daily_pnl += pnl
                    trades_today += 1
        
        equity_curve.append({"date": day_str, "equity": round(current_equity, 2)})

    # 4. Calculate Summary Metrics
    df_trades = pd.DataFrame(trades)
    
    total_trades = len(df_trades)
    win_rate = 0
    total_pnl = round(current_equity - initial_capital, 2)
    profit_factor = 0
    avg_win = 0
    avg_loss = 0
    
    if not df_trades.empty:
        wins = df_trades[df_trades['pnl'] > 0]
        losses = df_trades[df_trades['pnl'] <= 0]
        win_rate = round(len(wins) / total_trades * 100, 2) if total_trades > 0 else 0
        gross_win = wins['pnl'].sum()
        gross_loss = abs(losses['pnl'].sum())
        profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else float('inf')
        avg_win = round(wins['pnl'].mean(), 2) if len(wins) > 0 else 0
        avg_loss = round(losses['pnl'].mean(), 2) if len(losses) > 0 else 0

    results = {
        "metrics": {
            "total_trades": total_trades,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "profit_factor": profit_factor if profit_factor != float('inf') else 999.99,
            "final_equity": round(current_equity, 2),
            "initial_capital": initial_capital,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "return_pct": round((current_equity - initial_capital) / initial_capital * 100, 2)
        },
        "equity_curve": equity_curve,
        "trades": trades,
        "config_used": {
            "strategy_type": strategy_type,
            "anchor_min": anchor_min,
            "confirm_min": confirm_min,
            "rr": rr,
            "risk_per_trade": risk_per_trade
        }
    }
    
    return results
