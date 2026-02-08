"""
Strategy Optimization Script
Tests various filters and improvements to maximize win rate and profits.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time
from data_loader import fetch_historical_data, resample_data
from strategy import detect_fvgs, check_retest_and_engulf, STRAT_A

# Configuration
TICKER = "TSLA"
START_DATE = datetime(2025, 1, 1)
END_DATE = datetime(2026, 1, 30)
RISK_PER_TRADE = 100
INITIAL_CAPITAL = 1000.0

def run_optimized_backtest(
    df_1m, 
    strategy_type='A',
    # Optimization parameters
    max_entry_hour=16,           # Latest hour to enter trades (24h format)
    max_entry_minute=0,          # Latest minute
    min_fvg_size_pct=0.0,        # Minimum FVG size as % of price
    align_with_first_candle=False,  # Only trade in first candle direction
    min_first_candle_size_pct=0.0,  # Minimum first candle range as % of price
    require_volume_spike=False,  # Require above-average volume on FVG
):
    """
    Runs backtest with configurable optimization parameters.
    """
    strat_config = STRAT_A
    anchor_min = strat_config['anchor_min']
    confirm_min = strat_config['confirm_min']
    rr = strat_config['rr']
    
    df_anchor = resample_data(df_1m, anchor_min)
    df_confirm = resample_data(df_1m, confirm_min)
    
    trades = []
    current_equity = INITIAL_CAPITAL
    
    days = df_1m.index.normalize().unique()
    days = sorted(days)

    for day in days:
        if current_equity <= 0:
            continue

        daily_anchor = df_anchor[df_anchor.index.normalize() == day]
        daily_confirm = df_confirm[df_confirm.index.normalize() == day]
        daily_1m = df_1m[df_1m.index.normalize() == day]
        
        if daily_anchor.empty or daily_confirm.empty:
            continue
            
        # Get first candle
        candidates = daily_anchor.between_time('09:30', '09:30')
        if candidates.empty:
            continue
            
        first_candle = candidates.iloc[0]
        first_high = float(first_candle['High'])
        first_low = float(first_candle['Low'])
        first_open = float(first_candle['Open'])
        first_close = float(first_candle['Close'])
        first_candle_time = first_candle.name
        first_candle_close_time = first_candle_time + pd.Timedelta(minutes=anchor_min)
        
        # First candle characteristics
        first_candle_range = first_high - first_low
        first_candle_mid = (first_high + first_low) / 2
        first_candle_range_pct = (first_candle_range / first_candle_mid) * 100
        first_candle_bullish = first_close > first_open
        
        # Filter: Minimum first candle size
        if first_candle_range_pct < min_first_candle_size_pct:
            continue
        
        # Calculate average volume for volume spike detection
        avg_volume = daily_confirm['Volume'].mean() if not daily_confirm.empty else 0
        
        # Detect FVGs
        fvgs = detect_fvgs(daily_confirm)
        
        for fvg in fvgs:
            fvg_time = fvg["time"]
            fvg_type = fvg["type"]
            gap_size = fvg["gap_top"] - fvg["gap_bottom"]
            gap_mid = (fvg["gap_top"] + fvg["gap_bottom"]) / 2
            gap_size_pct = (gap_size / gap_mid) * 100
            
            # Filter: FVG must be after first candle closes
            if fvg_time <= first_candle_close_time:
                continue
            
            # Filter: Minimum FVG size
            if gap_size_pct < min_fvg_size_pct:
                continue
            
            # Filter: Volume spike on FVG candle
            if require_volume_spike:
                fvg_idx = fvg["candle_idx"]
                if fvg_idx < len(daily_confirm):
                    fvg_volume = daily_confirm.iloc[fvg_idx]['Volume']
                    if fvg_volume < avg_volume * 1.2:  # Require 20% above average
                        continue
            
            # Filter: Align with first candle direction
            if align_with_first_candle:
                if first_candle_bullish and fvg_type != "bullish":
                    continue
                if not first_candle_bullish and fvg_type != "bearish":
                    continue
            
            # Check for first candle break (Strategy A logic)
            pre_and_during_fvg = daily_confirm[daily_confirm.index <= fvg_time]
            if pre_and_during_fvg.empty:
                continue
                
            if fvg_type == "bullish":
                if pre_and_during_fvg['High'].max() <= first_high:
                    continue
            elif fvg_type == "bearish":
                if pre_and_during_fvg['Low'].min() >= first_low:
                    continue
            
            # Check retest and engulf
            signal = check_retest_and_engulf(daily_confirm, fvg)
            if signal is None:
                continue
            
            entry_time = signal['entry_time']
            
            # Filter: Maximum entry time
            entry_hour = entry_time.hour
            entry_minute = entry_time.minute
            if entry_hour > max_entry_hour or (entry_hour == max_entry_hour and entry_minute > max_entry_minute):
                continue
            
            # Execute trade
            entry_price = signal['entry_price']
            stop_price = signal['stop_price']
            direction = signal['direction']
            
            risk_dist = abs(entry_price - stop_price)
            if risk_dist == 0:
                continue

            target_price = entry_price + (risk_dist * rr) if direction == "LONG" else entry_price - (risk_dist * rr)
            actual_risk = min(RISK_PER_TRADE, current_equity)
            
            # Simulate trade outcome
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
                pnl = (last_price - entry_price) / risk_dist * actual_risk if direction == "LONG" else (entry_price - last_price) / risk_dist * actual_risk
                outcome = "TIMEOUT"
                
            trades.append({
                "entry_time": entry_time,
                "exit_time": exit_time,
                "direction": direction,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "stop_price": stop_price,
                "target_price": target_price,
                "outcome": outcome,
                "pnl": round(pnl, 2),
                "first_candle_bullish": first_candle_bullish,
                "fvg_type": fvg_type,
                "gap_size_pct": round(gap_size_pct, 3),
                "first_candle_range_pct": round(first_candle_range_pct, 3),
                "entry_hour": entry_hour
            })
            
            current_equity += pnl
            break  # One trade per day
    
    return trades, current_equity

def analyze_results(trades, label=""):
    """Analyze and print trade results."""
    df = pd.DataFrame(trades)
    
    if df.empty:
        print(f"{label}: No trades")
        return {"trades": 0, "win_rate": 0, "pnl": 0}
    
    total = len(df)
    wins = len(df[df['pnl'] > 0])
    losses = len(df[df['pnl'] <= 0])
    win_rate = (wins / total) * 100 if total > 0 else 0
    total_pnl = df['pnl'].sum()
    
    print(f"{label}")
    print(f"  Trades: {total}, Wins: {wins}, Losses: {losses}")
    print(f"  Win Rate: {win_rate:.2f}%")
    print(f"  Total PnL: ${total_pnl:,.2f}")
    print()
    
    return {"trades": total, "win_rate": win_rate, "pnl": total_pnl}

def run_rr_test(df_1m, rr_ratio, max_entry_hour=16, align_fc=False):
    """Test different R:R ratios."""
    strat_config = STRAT_A.copy()
    anchor_min = strat_config['anchor_min']
    confirm_min = strat_config['confirm_min']
    
    df_anchor = resample_data(df_1m, anchor_min)
    df_confirm = resample_data(df_1m, confirm_min)
    
    trades = []
    current_equity = INITIAL_CAPITAL
    
    days = df_1m.index.normalize().unique()
    days = sorted(days)

    for day in days:
        if current_equity <= 0:
            continue

        daily_anchor = df_anchor[df_anchor.index.normalize() == day]
        daily_confirm = df_confirm[df_confirm.index.normalize() == day]
        daily_1m = df_1m[df_1m.index.normalize() == day]
        
        if daily_anchor.empty or daily_confirm.empty:
            continue
            
        candidates = daily_anchor.between_time('09:30', '09:30')
        if candidates.empty:
            continue
            
        first_candle = candidates.iloc[0]
        first_high = float(first_candle['High'])
        first_low = float(first_candle['Low'])
        first_open = float(first_candle['Open'])
        first_close = float(first_candle['Close'])
        first_candle_time = first_candle.name
        first_candle_close_time = first_candle_time + pd.Timedelta(minutes=anchor_min)
        first_candle_bullish = first_close > first_open
        
        fvgs = detect_fvgs(daily_confirm)
        
        for fvg in fvgs:
            fvg_time = fvg["time"]
            fvg_type = fvg["type"]
            
            if fvg_time <= first_candle_close_time:
                continue
            
            if align_fc:
                if first_candle_bullish and fvg_type != "bullish":
                    continue
                if not first_candle_bullish and fvg_type != "bearish":
                    continue
            
            pre_and_during_fvg = daily_confirm[daily_confirm.index <= fvg_time]
            if pre_and_during_fvg.empty:
                continue
                
            if fvg_type == "bullish":
                if pre_and_during_fvg['High'].max() <= first_high:
                    continue
            elif fvg_type == "bearish":
                if pre_and_during_fvg['Low'].min() >= first_low:
                    continue
            
            signal = check_retest_and_engulf(daily_confirm, fvg)
            if signal is None:
                continue
            
            entry_time = signal['entry_time']
            if entry_time.hour > max_entry_hour:
                continue
            
            entry_price = signal['entry_price']
            stop_price = signal['stop_price']
            direction = signal['direction']
            
            risk_dist = abs(entry_price - stop_price)
            if risk_dist == 0:
                continue

            # Use the specified R:R ratio
            target_price = entry_price + (risk_dist * rr_ratio) if direction == "LONG" else entry_price - (risk_dist * rr_ratio)
            actual_risk = min(RISK_PER_TRADE, current_equity)
            
            future_prices = daily_1m[daily_1m.index > entry_time]
            outcome = "OPEN"
            pnl = 0
            
            for ts, row in future_prices.iterrows():
                current_high = row['High']
                current_low = row['Low']
                
                if direction == "LONG":
                    if current_low <= stop_price:
                        outcome = "LOSS"
                        pnl = -actual_risk
                        break
                    if current_high >= target_price:
                        outcome = "WIN"
                        pnl = actual_risk * rr_ratio
                        break
                elif direction == "SHORT":
                    if current_high >= stop_price:
                        outcome = "LOSS"
                        pnl = -actual_risk
                        break
                    if current_low <= target_price:
                        outcome = "WIN"
                        pnl = actual_risk * rr_ratio
                        break
            
            if outcome == "OPEN" and not future_prices.empty:
                last_price = future_prices.iloc[-1]['Close']
                pnl = (last_price - entry_price) / risk_dist * actual_risk if direction == "LONG" else (entry_price - last_price) / risk_dist * actual_risk
                outcome = "TIMEOUT"
                
            trades.append({"outcome": outcome, "pnl": round(pnl, 2)})
            current_equity += pnl
            break
    
    return trades, current_equity


def analyze_loss_patterns(trades):
    """Deep analysis of losing trades."""
    df = pd.DataFrame(trades)
    if df.empty:
        return
    
    print("\n--- Loss Pattern Analysis ---")
    
    # By direction
    if 'direction' in df.columns:
        print("\nBy Direction:")
        for direction in df['direction'].unique():
            subset = df[df['direction'] == direction]
            wins = len(subset[subset['pnl'] > 0])
            total = len(subset)
            wr = (wins / total * 100) if total > 0 else 0
            print(f"  {direction}: {wins}/{total} wins ({wr:.1f}%)")
    
    # By entry hour
    if 'entry_hour' in df.columns:
        print("\nBy Entry Hour:")
        for hour in sorted(df['entry_hour'].unique()):
            subset = df[df['entry_hour'] == hour]
            wins = len(subset[subset['pnl'] > 0])
            total = len(subset)
            wr = (wins / total * 100) if total > 0 else 0
            pnl = subset['pnl'].sum()
            print(f"  {hour:02d}:00: {wins}/{total} wins ({wr:.1f}%) | PnL: ${pnl:.0f}")
    
    # By FVG type
    if 'fvg_type' in df.columns:
        print("\nBy FVG Type:")
        for ftype in df['fvg_type'].unique():
            subset = df[df['fvg_type'] == ftype]
            wins = len(subset[subset['pnl'] > 0])
            total = len(subset)
            wr = (wins / total * 100) if total > 0 else 0
            print(f"  {ftype}: {wins}/{total} wins ({wr:.1f}%)")


def main():
    print("=" * 70)
    print("FCR Strategy Optimization - TSLA Analysis (Extended)")
    print("=" * 70)
    print()
    
    # Load data
    print("Loading TSLA data...")
    df_1m = fetch_historical_data(TICKER, START_DATE, END_DATE, 1)
    
    if df_1m.empty:
        print("No data found!")
        return
    
    print(f"Loaded {len(df_1m)} 1-minute bars")
    print()
    
    # Run baseline with detailed trade info
    print("=" * 70)
    print("BASELINE ANALYSIS")
    print("=" * 70)
    trades, equity = run_optimized_backtest(df_1m)
    baseline = analyze_results(trades, "Baseline")
    analyze_loss_patterns(trades)
    
    # Test different R:R ratios
    print("\n" + "=" * 70)
    print("R:R RATIO OPTIMIZATION")
    print("=" * 70)
    
    rr_results = []
    for rr in [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]:
        trades_rr, eq = run_rr_test(df_1m, rr)
        df_rr = pd.DataFrame(trades_rr)
        if not df_rr.empty:
            wins = len(df_rr[df_rr['pnl'] > 0])
            total = len(df_rr)
            wr = wins / total * 100
            pnl = df_rr['pnl'].sum()
            rr_results.append((rr, total, wr, pnl))
            print(f"  R:R {rr}: {total} trades, {wr:.1f}% win rate, ${pnl:,.2f} PnL")
    
    # Test R:R with time filter
    print("\n" + "=" * 70)
    print("R:R + TIME FILTER (Before 1 PM)")
    print("=" * 70)
    
    for rr in [1.5, 2.0, 2.5, 3.0]:
        trades_rr, eq = run_rr_test(df_1m, rr, max_entry_hour=13)
        df_rr = pd.DataFrame(trades_rr)
        if not df_rr.empty:
            wins = len(df_rr[df_rr['pnl'] > 0])
            total = len(df_rr)
            wr = wins / total * 100
            pnl = df_rr['pnl'].sum()
            print(f"  R:R {rr} + 1PM: {total} trades, {wr:.1f}% win rate, ${pnl:,.2f} PnL")
    
    # Try opposite direction (counter-trend)
    print("\n" + "=" * 70)
    print("COUNTER-TREND TEST (opposite of first candle direction)")
    print("=" * 70)
    trades_ct, eq = run_optimized_backtest(df_1m, align_with_first_candle=True)
    # The align filter was wrong - test the opposite
    
    # Best combination test
    print("\n" + "=" * 70)
    print("BEST COMBINATION TESTS")
    print("=" * 70)
    
    # Before 1PM seems best, let's refine
    for hour in [10, 11, 12, 13, 14]:
        trades_t, eq = run_optimized_backtest(df_1m, max_entry_hour=hour)
        df_t = pd.DataFrame(trades_t)
        if not df_t.empty:
            wins = len(df_t[df_t['pnl'] > 0])
            total = len(df_t)
            wr = wins / total * 100
            pnl = df_t['pnl'].sum()
            print(f"  Before {hour}:00: {total} trades, {wr:.1f}% win rate, ${pnl:,.2f} PnL")
    
    # Test higher R:R with time filters
    print("\n" + "=" * 70)
    print("HIGHER R:R WITH TIME FILTERS")
    print("=" * 70)
    
    all_configs = []
    for rr in [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]:
        for hour in [11, 12, 13, 16]:  # 16 = all day
            trades_t, eq = run_rr_test(df_1m, rr, max_entry_hour=hour)
            df_t = pd.DataFrame(trades_t)
            if not df_t.empty:
                wins = len(df_t[df_t['pnl'] > 0])
                total = len(df_t)
                wr = wins / total * 100
                pnl = df_t['pnl'].sum()
                time_label = "all day" if hour == 16 else f"before {hour}:00"
                all_configs.append((f"R:R {rr} + {time_label}", total, wr, pnl))
                print(f"  R:R {rr} + {time_label}: {total} trades, {wr:.1f}% wr, ${pnl:,.2f} PnL")
    
    # Final best config - sort all tested configurations
    print("\n" + "=" * 70)
    print("TOP 10 CONFIGURATIONS BY PNL")
    print("=" * 70)
    
    # Sort all configs by PnL
    all_configs_sorted = sorted(all_configs, key=lambda x: x[3], reverse=True)
    
    print("\nRanked by Total PnL:")
    print("-" * 70)
    for i, (name, trades, wr, pnl) in enumerate(all_configs_sorted[:10], 1):
        improvement = ((pnl - 10019.94) / 10019.94) * 100  # vs baseline
        print(f"  {i}. {name}")
        print(f"     Trades: {trades} | Win Rate: {wr:.1f}% | PnL: ${pnl:,.2f} (+{improvement:.0f}%)")
    
    best = all_configs_sorted[0]
    print("\n" + "=" * 70)
    print(f"BEST CONFIGURATION: {best[0]}")
    print(f"  Total Trades: {best[1]}")
    print(f"  Win Rate: {best[2]:.1f}%")
    print(f"  Total PnL: ${best[3]:,.2f}")
    print(f"  Improvement over baseline: +{((best[3] - 10019.94) / 10019.94) * 100:.0f}%")
    print("=" * 70)

if __name__ == "__main__":
    main()

