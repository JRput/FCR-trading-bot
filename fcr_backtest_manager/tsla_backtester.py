"""
TSLA Backtester - Dedicated backtester for the TSLA Trading Strategy.

Implements optimized parameters for TSLA:
- Entry time window: 9:30 - 11:30 (avoid midday chop)
- First candle quality filters
- FVG quality filters  
- Trailing stop to break-even after 1R profit
- R:R of 5.5:1 with $1000 risk and $5000 capital
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from data_loader import fetch_historical_data, resample_data
from tsla_strategy import (
    get_tsla_strategy_config,
    validate_entry_time,
    validate_first_candle,
    validate_fvg_quality,
    check_first_candle_alignment,
    simulate_trade_with_trailing,
    detect_fvgs_tsla,
    check_retest_and_engulf_tsla,
    filter_by_first_candle_break_tsla,
    get_strategy_label_tsla,
    TSLA_STRATEGY
)


def run_tsla_backtest(
    start_date: datetime,
    end_date: datetime,
    risk_per_trade: float = 1000.0,
    initial_capital: float = 5000.0,
    custom_rr: Optional[float] = None,
    use_filters: bool = True,
    use_trailing_be: bool = True
) -> Dict[str, Any]:
    """
    Runs the optimized TSLA backtest.
    
    Parameters:
    - start_date: Backtest start date
    - end_date: Backtest end date
    - risk_per_trade: Dollar amount risked per trade (default $1000)
    - initial_capital: Starting capital (default $5000)
    - custom_rr: Optional custom risk/reward ratio (default 5.5)
    - use_filters: Apply TSLA-specific quality filters
    - use_trailing_be: Use trailing stop to break-even after 1R
    
    Returns: Dictionary with metrics, equity_curve, and trades
    """
    ticker = "TSLA"
    config = get_tsla_strategy_config(custom_rr)
    
    anchor_min = config['anchor_min']
    confirm_min = config['confirm_min']
    rr = config['rr']
    
    # Fetch Data
    print(f"Fetching TSLA data from {start_date.date()} to {end_date.date()}...")
    df_1m = fetch_historical_data(ticker, start_date, end_date, 1)
    
    if df_1m.empty:
        return {
            "error": f"No data found for TSLA. Please check the date range.",
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
    
    # Trading loop
    trades = []
    equity_curve = [{"date": start_date.strftime('%Y-%m-%d'), "equity": initial_capital}]
    current_equity = initial_capital
    
    # Stats tracking
    filtered_by_time = 0
    filtered_by_fc = 0
    filtered_by_fvg = 0
    be_trades = 0
    
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
            
        # Get First Candle (Anchor - 5min at 9:30)
        candidates = daily_anchor.between_time('09:30', '09:30')
        if candidates.empty:
            equity_curve.append({"date": day_str, "equity": current_equity})
            continue
            
        first_candle = candidates.iloc[0]
        first_high = float(first_candle['High'])
        first_low = float(first_candle['Low'])
        first_open = float(first_candle['Open'])
        first_close = float(first_candle['Close'])
        
        first_candle_data = {
            'high': first_high,
            'low': first_low,
            'open': first_open,
            'close': first_close
        }
        
        # Calculate when the first candle closes
        first_candle_close_time = first_candle.name + pd.Timedelta(minutes=anchor_min)
        
        # Apply first candle quality filter
        if use_filters:
            current_price = first_close
            if not validate_first_candle(first_candle_data, current_price, config):
                filtered_by_fc += 1
                equity_curve.append({"date": day_str, "equity": current_equity})
                continue
        
        # Detect FVGs on confirmation timeframe
        fvgs = detect_fvgs_tsla(daily_confirm)
        
        trades_today = 0
        max_trades = config.get('max_trades_per_day', 1)
        
        for fvg in fvgs:
            if trades_today >= max_trades:
                break
                
            # Apply FVG quality filter
            if use_filters:
                if not validate_fvg_quality(fvg, first_close, config):
                    filtered_by_fvg += 1
                    continue
            
            # Check for retest and engulfing confirmation
            signal = check_retest_and_engulf_tsla(daily_confirm, fvg)
            
            if signal:
                # Apply entry time filter
                if use_filters:
                    if not validate_entry_time(signal['entry_time'], config):
                        filtered_by_time += 1
                        continue
                
                # Check first candle break confirmation
                if not filter_by_first_candle_break_tsla(
                    daily_confirm, first_high, first_low, signal,
                    first_candle_close_time
                ):
                    continue
                
                # Check direction alignment (optional)
                if use_filters and config.get('require_fc_alignment', False):
                    if not check_first_candle_alignment(
                        signal['direction'], first_candle_data, True
                    ):
                        continue
                
                # Trade execution
                entry_price = signal['entry_price']
                stop_price = signal['stop_price']
                direction = signal['direction']
                entry_time = signal['entry_time']
                
                risk_dist = abs(entry_price - stop_price)
                if risk_dist == 0:
                    continue

                target_price = (entry_price + (risk_dist * rr) if direction == "LONG" 
                                else entry_price - (risk_dist * rr))
                
                # Position sizing
                actual_risk = min(risk_per_trade, current_equity)
                
                # Get future prices for simulation
                future_prices = daily_1m[daily_1m.index > entry_time]
                
                # Simulate trade with optional trailing BE
                result = simulate_trade_with_trailing(
                    entry_price=entry_price,
                    stop_price=stop_price,
                    target_price=target_price,
                    direction=direction,
                    future_prices=future_prices,
                    risk_amount=actual_risk,
                    rr=rr,
                    use_trailing_be=use_trailing_be,
                    be_trigger_r=config.get('be_trigger_r', 1.0)
                )
                
                outcome = result['outcome']
                exit_price = result['exit_price']
                exit_time = result['exit_time']
                pnl = result['pnl']
                
                if outcome == "BE":
                    be_trades += 1
                
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
                    "fvg_type": fvg['type'],
                    "max_favorable_r": round(result.get('max_favorable_r', 0), 2),
                    "stop_moved_to_be": result.get('stop_moved_to_be', False)
                })
                
                current_equity += pnl
                trades_today += 1
        
        equity_curve.append({"date": day_str, "equity": round(current_equity, 2)})

    # Calculate Summary Metrics
    df_trades = pd.DataFrame(trades)
    
    total_trades = len(df_trades)
    win_rate = 0
    total_pnl = round(current_equity - initial_capital, 2)
    profit_factor = 0
    avg_win = 0
    avg_loss = 0
    
    if not df_trades.empty:
        wins = df_trades[df_trades['outcome'] == 'WIN']
        losses = df_trades[df_trades['outcome'] == 'LOSS']
        be_count = len(df_trades[df_trades['outcome'] == 'BE'])
        
        # Win rate calculation (BE trades don't count as wins or losses)
        decisive_trades = len(wins) + len(losses)
        win_rate = round(len(wins) / decisive_trades * 100, 2) if decisive_trades > 0 else 0
        
        gross_win = wins['pnl'].sum() if not wins.empty else 0
        gross_loss = abs(losses['pnl'].sum()) if not losses.empty else 0
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
            "return_pct": round((current_equity - initial_capital) / initial_capital * 100, 2),
            "be_trades": be_trades,
            "filtered_by_time": filtered_by_time,
            "filtered_by_fc": filtered_by_fc,
            "filtered_by_fvg": filtered_by_fvg
        },
        "equity_curve": equity_curve,
        "trades": trades,
        "config_used": {
            "strategy": "TSLA Trading Strategy",
            "anchor_min": anchor_min,
            "confirm_min": confirm_min,
            "rr": rr,
            "risk_per_trade": risk_per_trade,
            "use_filters": use_filters,
            "use_trailing_be": use_trailing_be
        }
    }
    
    return results


if __name__ == "__main__":
    # Test the TSLA strategy
    from datetime import datetime
    
    print("Running TSLA Trading Strategy Backtest...")
    print("=" * 60)
    
    # Test with recent data
    results = run_tsla_backtest(
        start_date=datetime(2023, 1, 1),
        end_date=datetime(2025, 12, 31),
        risk_per_trade=1000,
        initial_capital=5000,
        use_filters=True,
        use_trailing_be=True
    )
    
    print(f"\nResults:")
    print(f"  Total Trades: {results['metrics']['total_trades']}")
    print(f"  Win Rate: {results['metrics']['win_rate']}%")
    print(f"  Break-Even Trades: {results['metrics']['be_trades']}")
    print(f"  Total PnL: ${results['metrics']['total_pnl']}")
    print(f"  Final Equity: ${results['metrics']['final_equity']}")
    print(f"  Return: {results['metrics']['return_pct']}%")
    print(f"  Profit Factor: {results['metrics']['profit_factor']}")
    
    print(f"\nFiltering Stats:")
    print(f"  Filtered by Time: {results['metrics']['filtered_by_time']}")
    print(f"  Filtered by First Candle: {results['metrics']['filtered_by_fc']}")
    print(f"  Filtered by FVG Quality: {results['metrics']['filtered_by_fvg']}")

