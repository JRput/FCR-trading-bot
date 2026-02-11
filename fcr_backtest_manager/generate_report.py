"""
Generate detailed backtest report and pattern analysis for TSLA Strategy A
Date Range: 1/1/2023 - 31/12/2025
"""

import pandas as pd
import numpy as np
from datetime import datetime, time
from data_loader import fetch_historical_data, resample_data
from strategy import detect_fvgs, check_retest_and_engulf, filter_by_first_candle_break, STRAT_A

# Configuration
TICKER = "TSLA"
START_DATE = datetime(2019, 1, 1)
END_DATE = datetime(2021, 12, 31)
RISK_PER_TRADE = 1000
INITIAL_CAPITAL = 5000.0
RR_RATIO = 5.5

def run_detailed_analysis():
    """Run backtest with detailed trade analysis."""
    
    print("=" * 80)
    print("FCR STRATEGY A - DETAILED PATTERN ANALYSIS")
    print(f"Ticker: {TICKER}")
    print(f"Period: {START_DATE.date()} to {END_DATE.date()}")
    print(f"R:R Ratio: {RR_RATIO}")
    print("=" * 80)
    
    # Load data
    print("\nLoading data...")
    df_1m = fetch_historical_data(TICKER, START_DATE, END_DATE, 1)
    
    if df_1m.empty:
        print("No data found!")
        return None
    
    print(f"Loaded {len(df_1m):,} 1-minute bars")
    
    # Strategy config
    anchor_min = STRAT_A['anchor_min']  # 5 min
    confirm_min = STRAT_A['confirm_min']  # 1 min
    
    df_anchor = resample_data(df_1m, anchor_min)
    df_confirm = resample_data(df_1m, confirm_min)
    
    trades = []
    current_equity = INITIAL_CAPITAL
    
    days = df_1m.index.normalize().unique()
    days = sorted(days)
    
    print(f"Analyzing {len(days)} trading days...")
    
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
        first_volume = float(first_candle['Volume'])
        first_candle_time = first_candle.name
        first_candle_close_time = first_candle_time + pd.Timedelta(minutes=anchor_min)
        
        # First candle characteristics
        first_candle_range = first_high - first_low
        first_candle_mid = (first_high + first_low) / 2
        first_candle_range_pct = (first_candle_range / first_candle_mid) * 100 if first_candle_mid > 0 else 0
        first_candle_bullish = first_close > first_open
        first_candle_body = abs(first_close - first_open)
        first_candle_body_ratio = (first_candle_body / first_candle_range) if first_candle_range > 0 else 0
        
        # Day's price action context
        day_open = daily_1m.iloc[0]['Open'] if not daily_1m.empty else 0
        day_high = daily_1m['High'].max()
        day_low = daily_1m['Low'].min()
        day_close = daily_1m.iloc[-1]['Close'] if not daily_1m.empty else 0
        day_range = day_high - day_low
        day_bullish = day_close > day_open
        
        # Detect FVGs
        fvgs = detect_fvgs(daily_confirm)
        
        for fvg in fvgs:
            fvg_time = fvg["time"]
            fvg_type = fvg["type"]
            gap_size = fvg["gap_top"] - fvg["gap_bottom"]
            gap_mid = (fvg["gap_top"] + fvg["gap_bottom"]) / 2
            gap_size_pct = (gap_size / gap_mid) * 100 if gap_mid > 0 else 0
            
            # FVG must be after first candle closes
            if fvg_time <= first_candle_close_time:
                continue
            
            # Check first candle break (Strategy A)
            pre_fvg = daily_confirm[daily_confirm.index <= fvg_time]
            if pre_fvg.empty:
                continue
            
            if fvg_type == "bullish":
                if pre_fvg['High'].max() <= first_high:
                    continue
            elif fvg_type == "bearish":
                if pre_fvg['Low'].min() >= first_low:
                    continue
            
            # Check retest and engulf
            signal = check_retest_and_engulf(daily_confirm, fvg)
            if signal is None:
                continue
            
            # Verify with filter
            if not filter_by_first_candle_break(
                daily_confirm, first_high, first_low, signal,
                strategy_type='A', first_candle_close_time=first_candle_close_time
            ):
                continue
            
            # Execute trade
            entry_time = signal['entry_time']
            entry_price = signal['entry_price']
            stop_price = signal['stop_price']
            direction = signal['direction']
            
            entry_hour = entry_time.hour
            entry_minute = entry_time.minute
            
            risk_dist = abs(entry_price - stop_price)
            if risk_dist == 0:
                continue
            
            risk_pct = (risk_dist / entry_price) * 100
            target_price = entry_price + (risk_dist * RR_RATIO) if direction == "LONG" else entry_price - (risk_dist * RR_RATIO)
            actual_risk = min(RISK_PER_TRADE, current_equity)
            
            # Simulate trade
            future_prices = daily_1m[daily_1m.index > entry_time]
            outcome = "OPEN"
            pnl = 0
            exit_time = None
            exit_price = None
            max_favorable = 0
            max_adverse = 0
            time_in_trade = 0
            
            for ts, row in future_prices.iterrows():
                time_in_trade += 1
                
                if direction == "LONG":
                    favorable = row['High'] - entry_price
                    adverse = entry_price - row['Low']
                    max_favorable = max(max_favorable, favorable)
                    max_adverse = max(max_adverse, adverse)
                    
                    if row['Low'] <= stop_price:
                        outcome = "LOSS"
                        exit_price = stop_price
                        exit_time = ts
                        pnl = -actual_risk
                        break
                    if row['High'] >= target_price:
                        outcome = "WIN"
                        exit_price = target_price
                        exit_time = ts
                        pnl = actual_risk * RR_RATIO
                        break
                else:
                    favorable = entry_price - row['Low']
                    adverse = row['High'] - entry_price
                    max_favorable = max(max_favorable, favorable)
                    max_adverse = max(max_adverse, adverse)
                    
                    if row['High'] >= stop_price:
                        outcome = "LOSS"
                        exit_price = stop_price
                        exit_time = ts
                        pnl = -actual_risk
                        break
                    if row['Low'] <= target_price:
                        outcome = "WIN"
                        exit_price = target_price
                        exit_time = ts
                        pnl = actual_risk * RR_RATIO
                        break
            
            if outcome == "OPEN" and not future_prices.empty:
                last_price = future_prices.iloc[-1]['Close']
                exit_time = future_prices.index[-1]
                exit_price = last_price
                pnl = (last_price - entry_price) / risk_dist * actual_risk if direction == "LONG" else (entry_price - last_price) / risk_dist * actual_risk
                outcome = "TIMEOUT"
            
            # Calculate R-multiples
            max_favorable_r = max_favorable / risk_dist if risk_dist > 0 else 0
            max_adverse_r = max_adverse / risk_dist if risk_dist > 0 else 0
            
            # Alignment analysis
            aligned_with_fc = (first_candle_bullish and fvg_type == "bullish") or (not first_candle_bullish and fvg_type == "bearish")
            aligned_with_day = (day_bullish and fvg_type == "bullish") or (not day_bullish and fvg_type == "bearish")
            
            trades.append({
                "date": day.strftime('%Y-%m-%d'),
                "day_of_week": day.strftime('%A'),
                "entry_time": entry_time.strftime('%H:%M'),
                "entry_hour": entry_hour,
                "direction": direction,
                "fvg_type": fvg_type,
                "entry_price": round(entry_price, 2),
                "stop_price": round(stop_price, 2),
                "target_price": round(target_price, 2),
                "exit_price": round(exit_price, 2) if exit_price else None,
                "outcome": outcome,
                "pnl": round(pnl, 2),
                # First candle characteristics
                "fc_bullish": first_candle_bullish,
                "fc_range_pct": round(first_candle_range_pct, 3),
                "fc_body_ratio": round(first_candle_body_ratio, 3),
                # FVG characteristics
                "fvg_size_pct": round(gap_size_pct, 3),
                # Risk characteristics
                "risk_pct": round(risk_pct, 3),
                # Trade dynamics
                "max_favorable_r": round(max_favorable_r, 2),
                "max_adverse_r": round(max_adverse_r, 2),
                "time_in_trade_min": time_in_trade,
                # Alignment
                "aligned_with_fc": aligned_with_fc,
                "aligned_with_day": aligned_with_day,
                # Day context
                "day_bullish": day_bullish,
                "day_range_pct": round((day_range / day_open) * 100, 3) if day_open > 0 else 0
            })
            
            current_equity += pnl
            break  # One trade per day
    
    return pd.DataFrame(trades), current_equity


def analyze_patterns(df):
    """Analyze winning and losing patterns."""
    
    wins = df[df['outcome'] == 'WIN']
    losses = df[df['outcome'] == 'LOSS']
    timeouts = df[df['outcome'] == 'TIMEOUT']
    
    report = []
    report.append("=" * 80)
    report.append("STRATEGY A PATTERN ANALYSIS REPORT")
    report.append(f"TSLA: January 1, 2019 - December 31, 2021")
    report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report.append("=" * 80)
    
    # Overall Statistics
    report.append("\n" + "=" * 80)
    report.append("SECTION 1: OVERALL STATISTICS")
    report.append("=" * 80)
    report.append(f"\nTotal Trades: {len(df)}")
    report.append(f"Wins: {len(wins)} ({len(wins)/len(df)*100:.1f}%)")
    report.append(f"Losses: {len(losses)} ({len(losses)/len(df)*100:.1f}%)")
    report.append(f"Timeouts: {len(timeouts)} ({len(timeouts)/len(df)*100:.1f}%)")
    report.append(f"Total PnL: ${df['pnl'].sum():,.2f}")
    report.append(f"Average Win: ${wins['pnl'].mean():,.2f}" if len(wins) > 0 else "Average Win: N/A")
    report.append(f"Average Loss: ${losses['pnl'].mean():,.2f}" if len(losses) > 0 else "Average Loss: N/A")
    
    # Pattern Analysis by Entry Hour
    report.append("\n" + "=" * 80)
    report.append("SECTION 2: PATTERN ANALYSIS BY ENTRY HOUR")
    report.append("=" * 80)
    report.append("\n{:^8} | {:^8} | {:^8} | {:^10} | {:^12}".format("Hour", "Trades", "Wins", "Win Rate", "PnL"))
    report.append("-" * 55)
    
    for hour in sorted(df['entry_hour'].unique()):
        subset = df[df['entry_hour'] == hour]
        w = len(subset[subset['outcome'] == 'WIN'])
        t = len(subset)
        wr = w/t*100 if t > 0 else 0
        pnl = subset['pnl'].sum()
        marker = " *** HIGH" if wr >= 40 else (" ** LOW" if wr < 25 else "")
        report.append(f"{hour:02d}:00    | {t:^8} | {w:^8} | {wr:^9.1f}% | ${pnl:>10,.2f}{marker}")
    
    # Pattern Analysis by Direction
    report.append("\n" + "=" * 80)
    report.append("SECTION 3: PATTERN ANALYSIS BY DIRECTION")
    report.append("=" * 80)
    
    for direction in ['LONG', 'SHORT']:
        subset = df[df['direction'] == direction]
        w = len(subset[subset['outcome'] == 'WIN'])
        t = len(subset)
        wr = w/t*100 if t > 0 else 0
        pnl = subset['pnl'].sum()
        report.append(f"\n{direction}:")
        report.append(f"  Trades: {t} | Wins: {w} | Win Rate: {wr:.1f}% | PnL: ${pnl:,.2f}")
    
    # Pattern Analysis by Day of Week
    report.append("\n" + "=" * 80)
    report.append("SECTION 4: PATTERN ANALYSIS BY DAY OF WEEK")
    report.append("=" * 80)
    
    day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    for day in day_order:
        subset = df[df['day_of_week'] == day]
        if subset.empty:
            continue
        w = len(subset[subset['outcome'] == 'WIN'])
        t = len(subset)
        wr = w/t*100 if t > 0 else 0
        pnl = subset['pnl'].sum()
        marker = " *** BEST" if wr >= 35 else (" ** WORST" if wr < 22 else "")
        report.append(f"\n{day}:")
        report.append(f"  Trades: {t} | Wins: {w} | Win Rate: {wr:.1f}% | PnL: ${pnl:,.2f}{marker}")
    
    # Pattern Analysis by First Candle Alignment
    report.append("\n" + "=" * 80)
    report.append("SECTION 5: FIRST CANDLE ALIGNMENT ANALYSIS")
    report.append("=" * 80)
    
    for aligned in [True, False]:
        subset = df[df['aligned_with_fc'] == aligned]
        label = "Aligned with First Candle" if aligned else "Counter to First Candle"
        w = len(subset[subset['outcome'] == 'WIN'])
        t = len(subset)
        wr = w/t*100 if t > 0 else 0
        pnl = subset['pnl'].sum()
        report.append(f"\n{label}:")
        report.append(f"  Trades: {t} | Wins: {w} | Win Rate: {wr:.1f}% | PnL: ${pnl:,.2f}")
    
    # Pattern Analysis by First Candle Characteristics
    report.append("\n" + "=" * 80)
    report.append("SECTION 6: FIRST CANDLE CHARACTERISTICS")
    report.append("=" * 80)
    
    # By Range Size
    report.append("\nBy First Candle Range Size:")
    bins = [0, 0.5, 1.0, 1.5, 2.0, 3.0, 100]
    labels = ['<0.5%', '0.5-1%', '1-1.5%', '1.5-2%', '2-3%', '>3%']
    df['fc_range_bucket'] = pd.cut(df['fc_range_pct'], bins=bins, labels=labels)
    
    for bucket in labels:
        subset = df[df['fc_range_bucket'] == bucket]
        if subset.empty:
            continue
        w = len(subset[subset['outcome'] == 'WIN'])
        t = len(subset)
        wr = w/t*100 if t > 0 else 0
        pnl = subset['pnl'].sum()
        marker = " *** HIGH" if wr >= 35 else ""
        report.append(f"  {bucket}: {t} trades | {w} wins | {wr:.1f}% win rate | ${pnl:,.2f}{marker}")
    
    # By Body Ratio
    report.append("\nBy First Candle Body Ratio:")
    bins = [0, 0.3, 0.5, 0.7, 1.0]
    labels = ['<30%', '30-50%', '50-70%', '>70%']
    df['fc_body_bucket'] = pd.cut(df['fc_body_ratio'], bins=bins, labels=labels)
    
    for bucket in labels:
        subset = df[df['fc_body_bucket'] == bucket]
        if subset.empty:
            continue
        w = len(subset[subset['outcome'] == 'WIN'])
        t = len(subset)
        wr = w/t*100 if t > 0 else 0
        pnl = subset['pnl'].sum()
        marker = " *** HIGH" if wr >= 35 else ""
        report.append(f"  {bucket} body: {t} trades | {w} wins | {wr:.1f}% win rate | ${pnl:,.2f}{marker}")
    
    # Max Favorable Excursion Analysis
    report.append("\n" + "=" * 80)
    report.append("SECTION 7: LOSS ANALYSIS - MAX FAVORABLE EXCURSION")
    report.append("=" * 80)
    
    if len(losses) > 0:
        avg_mfe = losses['max_favorable_r'].mean()
        report.append(f"\nAverage MFE for losses: {avg_mfe:.2f}R")
        
        mfe_1r = len(losses[losses['max_favorable_r'] >= 1.0])
        mfe_2r = len(losses[losses['max_favorable_r'] >= 2.0])
        mfe_3r = len(losses[losses['max_favorable_r'] >= 3.0])
        
        report.append(f"Losses that reached 1R profit first: {mfe_1r}/{len(losses)} ({mfe_1r/len(losses)*100:.1f}%)")
        report.append(f"Losses that reached 2R profit first: {mfe_2r}/{len(losses)} ({mfe_2r/len(losses)*100:.1f}%)")
        report.append(f"Losses that reached 3R profit first: {mfe_3r}/{len(losses)} ({mfe_3r/len(losses)*100:.1f}%)")
        
        report.append("\n>> INSIGHT: These losses went into profit before reversing.")
        if mfe_1r / len(losses) > 0.3:
            report.append(">> RECOMMENDATION: Consider trailing stop to break-even after 1R profit.")
    
    # Winning Pattern Summary
    report.append("\n" + "=" * 80)
    report.append("SECTION 8: WINNING TRADE PATTERNS (WHAT WORKS)")
    report.append("=" * 80)
    
    if len(wins) > 0:
        # Find best entry hours
        best_hours = []
        for hour in sorted(df['entry_hour'].unique()):
            subset = df[df['entry_hour'] == hour]
            if len(subset) >= 10:  # Minimum sample
                wr = len(subset[subset['outcome'] == 'WIN']) / len(subset) * 100
                if wr >= 35:
                    best_hours.append((hour, wr, len(subset)))
        
        report.append("\n1. BEST ENTRY TIMES:")
        if best_hours:
            for h, wr, n in sorted(best_hours, key=lambda x: -x[1]):
                report.append(f"   - {h:02d}:00 hour: {wr:.1f}% win rate ({n} trades)")
        else:
            report.append("   - No entry hour shows >35% win rate with sufficient trades")
        
        # Average characteristics of wins
        report.append("\n2. WINNING TRADE CHARACTERISTICS:")
        report.append(f"   - Average First Candle Range: {wins['fc_range_pct'].mean():.2f}%")
        report.append(f"   - Average First Candle Body Ratio: {wins['fc_body_ratio'].mean():.2f}")
        report.append(f"   - Average FVG Size: {wins['fvg_size_pct'].mean():.3f}%")
        report.append(f"   - Average Risk (stop distance): {wins['risk_pct'].mean():.3f}%")
        report.append(f"   - Average Time in Trade: {wins['time_in_trade_min'].mean():.0f} minutes")
        
        # Direction breakdown for wins
        long_wins = len(wins[wins['direction'] == 'LONG'])
        short_wins = len(wins[wins['direction'] == 'SHORT'])
        report.append(f"\n3. WINNING DIRECTION BREAKDOWN:")
        report.append(f"   - LONG wins: {long_wins} ({long_wins/len(wins)*100:.1f}%)")
        report.append(f"   - SHORT wins: {short_wins} ({short_wins/len(wins)*100:.1f}%)")
    
    # Losing Pattern Summary
    report.append("\n" + "=" * 80)
    report.append("SECTION 9: LOSING TRADE PATTERNS (WHAT DOESN'T WORK)")
    report.append("=" * 80)
    
    if len(losses) > 0:
        # Find worst entry hours
        worst_hours = []
        for hour in sorted(df['entry_hour'].unique()):
            subset = df[df['entry_hour'] == hour]
            if len(subset) >= 10:
                wr = len(subset[subset['outcome'] == 'WIN']) / len(subset) * 100
                if wr < 25:
                    worst_hours.append((hour, wr, len(subset)))
        
        report.append("\n1. WORST ENTRY TIMES:")
        if worst_hours:
            for h, wr, n in sorted(worst_hours, key=lambda x: x[1]):
                report.append(f"   - {h:02d}:00 hour: {wr:.1f}% win rate ({n} trades) - AVOID")
        else:
            report.append("   - No entry hour shows <25% win rate consistently")
        
        # Average characteristics of losses
        report.append("\n2. LOSING TRADE CHARACTERISTICS:")
        report.append(f"   - Average First Candle Range: {losses['fc_range_pct'].mean():.2f}%")
        report.append(f"   - Average First Candle Body Ratio: {losses['fc_body_ratio'].mean():.2f}")
        report.append(f"   - Average FVG Size: {losses['fvg_size_pct'].mean():.3f}%")
        report.append(f"   - Average Risk (stop distance): {losses['risk_pct'].mean():.3f}%")
        report.append(f"   - Average Time in Trade: {losses['time_in_trade_min'].mean():.0f} minutes")
        
        # Quick reversals
        quick_losses = losses[losses['time_in_trade_min'] <= 10]
        report.append(f"\n3. QUICK REVERSAL LOSSES (stopped out within 10 min):")
        report.append(f"   - Count: {len(quick_losses)} ({len(quick_losses)/len(losses)*100:.1f}% of all losses)")
    
    # Recommendations
    report.append("\n" + "=" * 80)
    report.append("SECTION 10: RECOMMENDATIONS TO IMPROVE WIN RATE")
    report.append("=" * 80)
    
    # Calculate which filters would help
    recommendations = []
    
    # Time filter analysis
    morning_trades = df[df['entry_hour'] <= 10]
    if len(morning_trades) >= 50:
        morning_wr = len(morning_trades[morning_trades['outcome'] == 'WIN']) / len(morning_trades) * 100
        overall_wr = len(wins) / len(df) * 100
        if morning_wr > overall_wr + 3:
            recommendations.append(f"1. TIME FILTER: Trade only before 11:00 AM (would increase win rate from {overall_wr:.1f}% to {morning_wr:.1f}%)")
    
    # Body ratio analysis
    strong_body = df[df['fc_body_ratio'] >= 0.5]
    if len(strong_body) >= 50:
        strong_wr = len(strong_body[strong_body['outcome'] == 'WIN']) / len(strong_body) * 100
        if strong_wr > overall_wr + 3:
            recommendations.append(f"2. FIRST CANDLE FILTER: Only trade when first candle body >= 50% of range (would increase win rate to {strong_wr:.1f}%)")
    
    # R:R analysis
    report.append("\n3. R:R RATIO CONSIDERATION:")
    report.append(f"   Current R:R: {RR_RATIO}")
    report.append("   Lower R:R (e.g., 2.0-3.0) would increase win rate but reduce per-trade profit")
    report.append("   Analysis: With current {:.1f}:1 R:R, you need only {:.1f}% win rate to break even".format(
        RR_RATIO, 100 / (RR_RATIO + 1)))
    
    if recommendations:
        report.append("\nSUGGESTED FILTERS:")
        for rec in recommendations:
            report.append(f"   {rec}")
    
    # Trade-by-Trade Sample
    report.append("\n" + "=" * 80)
    report.append("SECTION 11: SAMPLE TRADES (First 20 Wins and First 20 Losses)")
    report.append("=" * 80)
    
    report.append("\n--- SAMPLE WINNING TRADES ---")
    report.append("{:<12} | {:^6} | {:^5} | {:^8} | {:^8} | {:^8} | {:^6}".format(
        "Date", "Time", "Dir", "Entry", "Exit", "FC%", "FVG%"))
    report.append("-" * 75)
    
    for _, row in wins.head(20).iterrows():
        report.append("{:<12} | {:^6} | {:^5} | {:^8.2f} | {:^8.2f} | {:^6.2f} | {:^6.3f}".format(
            row['date'], row['entry_time'], row['direction'], 
            row['entry_price'], row['exit_price'] if row['exit_price'] else 0,
            row['fc_range_pct'], row['fvg_size_pct']))
    
    report.append("\n--- SAMPLE LOSING TRADES ---")
    report.append("{:<12} | {:^6} | {:^5} | {:^8} | {:^8} | {:^6} | {:^6} | {:^6}".format(
        "Date", "Time", "Dir", "Entry", "Exit", "FC%", "FVG%", "MFE(R)"))
    report.append("-" * 85)
    
    for _, row in losses.head(20).iterrows():
        report.append("{:<12} | {:^6} | {:^5} | {:^8.2f} | {:^8.2f} | {:^6.2f} | {:^6.3f} | {:^6.2f}".format(
            row['date'], row['entry_time'], row['direction'],
            row['entry_price'], row['exit_price'] if row['exit_price'] else 0,
            row['fc_range_pct'], row['fvg_size_pct'], row['max_favorable_r']))
    
    report.append("\n" + "=" * 80)
    report.append("END OF REPORT")
    report.append("=" * 80)
    
    return "\n".join(report)


def main():
    # Run analysis
    trades_df, final_equity = run_detailed_analysis()
    
    if trades_df is None or trades_df.empty:
        print("No trades generated!")
        return
    
    print(f"\nAnalyzed {len(trades_df)} trades")
    print(f"Final Equity: ${final_equity:,.2f}")
    
    # Generate report
    report = analyze_patterns(trades_df)
    
    # Save report
    report_path = "/Users/Sushil/Documents/Documents/IMT2/Side hustle/FCR trading strategy_2/TSLA strategy A summary 1.1.19-31.12.21.txt"
    with open(report_path, 'w') as f:
        f.write(report)
    
    print(f"\nReport saved to: {report_path}")
    print("\n" + "=" * 50)
    print("QUICK SUMMARY")
    print("=" * 50)
    
    wins = len(trades_df[trades_df['outcome'] == 'WIN'])
    losses = len(trades_df[trades_df['outcome'] == 'LOSS'])
    total = len(trades_df)
    
    print(f"Win Rate: {wins/total*100:.1f}%")
    print(f"Total PnL: ${trades_df['pnl'].sum():,.2f}")
    
    # Also save trades to CSV for further analysis
    csv_path = "/Users/Sushil/Documents/Documents/IMT2/Side hustle/FCR trading strategy_2/TSLA strategy A trades 1.1.19-31.12.21.csv"
    trades_df.to_csv(csv_path, index=False)
    print(f"Trade data saved to: {csv_path}")


if __name__ == "__main__":
    main()

