#!/usr/bin/env python3
# =============================================================
# STOP LOSS CALIBRATION — LIQUIDATION SQUEEZE STRATEGY
# Tests multiple stop levels against the 20 MAGI-approved
# signals in magi_backtest_decisions to find optimal parameters.
# =============================================================

import sys, os
sys.path.insert(0, '/root/eth_observer')
os.chdir('/root/eth_observer')

import sqlite3
import statistics

DB_PATH = '/root/eth_observer/observer.db'
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# Load MAGI-approved SHORT trades
approved = conn.execute("""
    SELECT signal_timestamp, entry_price, price_4h_return,
           short_liq_usd, short_long_ratio, fear_greed_at_signal,
           melchior_conviction, balthasar_conviction, casper_conviction
    FROM magi_backtest_decisions
    WHERE consensus_result = 'short'
    ORDER BY signal_timestamp
""").fetchall()

print(f"MAGI-approved short signals: {len(approved)}")

# Constants
TARGET_PCT  = 0.0200   # fixed -2.0% target throughout
TAKER_RT    = 0.0018   # 0.18% round-trip taker fees
MAX_HOURS   = 12

# Stop levels to test (as % above entry for short)
stop_levels = [0.0050, 0.0075, 0.0100, 0.0125, 0.0150, 0.0175, 0.0200, 0.0250, 0.0300]

def avg(lst):
    clean = [x for x in lst if x is not None]
    return statistics.mean(clean) if clean else None

def expectancy(net_list):
    if not net_list:
        return None
    w = [x for x in net_list if x >= 0]
    l = [x for x in net_list if x < 0]
    wr = len(w) / len(net_list)
    avg_w = avg(w) or 0
    avg_l = avg(l) or 0
    return wr * avg_w + (1 - wr) * avg_l

print("\nPre-loading forward prices for each signal...")
# Pre-load forward bars for each signal
signal_fwd = {}
for row in approved:
    sig_ts    = row['signal_timestamp']
    fwd_rows  = conn.execute("""
        SELECT timestamp, eth_close FROM backtest_results
        WHERE timestamp > ? ORDER BY timestamp ASC LIMIT 12
    """, (sig_ts,)).fetchall()
    signal_fwd[sig_ts] = [(r['timestamp'], r['eth_close']) for r in fwd_rows]

print(f"  Loaded forward data for {len(signal_fwd)} signals\n")

# ── Main calibration loop ───────────────────────────────────────
print("=" * 80)
print(f"  {'Stop%':>6}  {'WinRate':>8}  {'AvgGross':>10}  {'AvgNet':>10}  "
      f"{'Expectancy':>12}  {'Tgt':>4}  {'Stp':>4}  {'Tm':>4}")
print("  " + "-" * 74)

results_by_stop = {}

for stop_pct in stop_levels:
    net_rets   = []
    gross_rets = []
    exit_tgt   = 0
    exit_stp   = 0
    exit_tm    = 0

    for row in approved:
        sig_ts    = row['signal_timestamp']
        entry     = row['entry_price']
        if not entry:
            continue

        stop_price   = entry * (1 + stop_pct)
        target_price = entry * (1 - TARGET_PCT)

        # NFA flat fee: $0.30 round trip on 0.1 ETH contract
        nfa_rt_pct = (0.30 / (0.1 * entry))
        fees_pct   = TAKER_RT + nfa_rt_pct

        exit_price  = None
        exit_reason = None

        for ts, p in signal_fwd[sig_ts]:
            if p is None:
                continue
            if p >= stop_price:
                exit_price  = p
                exit_reason = 'stop'
                break
            if p <= target_price:
                exit_price  = p
                exit_reason = 'target'
                break
        else:
            fwd = signal_fwd[sig_ts]
            if fwd:
                exit_price  = fwd[-1][1]
                exit_reason = 'time'

        if exit_price and entry:
            gross = (entry - exit_price) / entry * 100   # short P&L
            net   = gross - fees_pct * 100
            gross_rets.append(gross)
            net_rets.append(net)

            if exit_reason == 'target':
                exit_tgt += 1
            elif exit_reason == 'stop':
                exit_stp += 1
            else:
                exit_tm += 1

    n_trades = len(net_rets)
    n_wins   = sum(1 for x in net_rets if x >= 0)
    wr       = n_wins / n_trades * 100 if n_trades else 0
    avg_g    = avg(gross_rets)
    avg_n    = avg(net_rets)
    exp      = expectancy(net_rets)

    results_by_stop[stop_pct] = {
        'win_rate':   wr,
        'avg_gross':  avg_g,
        'avg_net':    avg_n,
        'expectancy': exp,
        'exit_tgt':   exit_tgt,
        'exit_stp':   exit_stp,
        'exit_tm':    exit_tm,
        'n':          n_trades,
    }

    print(f"  {stop_pct*100:5.2f}%  "
          f"{wr:7.1f}%  "
          f"{(avg_g or 0):+9.3f}%  "
          f"{(avg_n or 0):+9.3f}%  "
          f"{(exp or 0):+11.3f}%  "
          f"{exit_tgt:4d}  {exit_stp:4d}  {exit_tm:4d}")

print("=" * 80)

# ── Also test variable target levels at the best-looking stop ───
print("\n\nTARGET SENSITIVITY (varying target with stop fixed at 1.50%)")
print("=" * 80)
FIXED_STOP  = 0.0150
target_levels = [0.0100, 0.0125, 0.0150, 0.0175, 0.0200, 0.0225, 0.0250, 0.0300]

print(f"  {'Target%':>8}  {'WinRate':>8}  {'AvgGross':>10}  {'AvgNet':>10}  "
      f"{'Expectancy':>12}  {'Tgt':>4}  {'Stp':>4}  {'Tm':>4}")
print("  " + "-" * 74)

for tgt_pct in target_levels:
    net_rets   = []
    gross_rets = []
    exit_tgt   = 0
    exit_stp   = 0
    exit_tm    = 0

    for row in approved:
        sig_ts    = row['signal_timestamp']
        entry     = row['entry_price']
        if not entry:
            continue

        stop_price   = entry * (1 + FIXED_STOP)
        target_price = entry * (1 - tgt_pct)

        nfa_rt_pct = (0.30 / (0.1 * entry))
        fees_pct   = TAKER_RT + nfa_rt_pct

        exit_price  = None
        exit_reason = None

        for ts, p in signal_fwd[sig_ts]:
            if p is None:
                continue
            if p >= stop_price:
                exit_price  = p
                exit_reason = 'stop'
                break
            if p <= target_price:
                exit_price  = p
                exit_reason = 'target'
                break
        else:
            fwd = signal_fwd[sig_ts]
            if fwd:
                exit_price  = fwd[-1][1]
                exit_reason = 'time'

        if exit_price and entry:
            gross = (entry - exit_price) / entry * 100
            net   = gross - fees_pct * 100
            gross_rets.append(gross)
            net_rets.append(net)

            if exit_reason == 'target':
                exit_tgt += 1
            elif exit_reason == 'stop':
                exit_stp += 1
            else:
                exit_tm += 1

    n_trades = len(net_rets)
    n_wins   = sum(1 for x in net_rets if x >= 0)
    wr       = n_wins / n_trades * 100 if n_trades else 0
    avg_g    = avg(gross_rets)
    avg_n    = avg(net_rets)
    exp      = expectancy(net_rets)

    print(f"  {tgt_pct*100:7.2f}%  "
          f"{wr:7.1f}%  "
          f"{(avg_g or 0):+9.3f}%  "
          f"{(avg_n or 0):+9.3f}%  "
          f"{(exp or 0):+11.3f}%  "
          f"{exit_tgt:4d}  {exit_stp:4d}  {exit_tm:4d}")

print("=" * 80)

# ── Delayed entry: enter on next bar instead of immediately ─────
print("\n\nDELAYED ENTRY SENSITIVITY (enter on bar N+1, N+2, N+3 vs immediate)")
print("  Stop 1.50%, Target 2.0%\n")

FIXED_STOP_DE  = 0.0150
FIXED_TGT_DE   = 0.0200

print(f"  {'Delay':>6}  {'WinRate':>8}  {'AvgGross':>10}  {'AvgNet':>10}  "
      f"{'Expectancy':>12}  {'Tgt':>4}  {'Stp':>4}  {'Tm':>4}")
print("  " + "-" * 70)

for delay_bars in [0, 1, 2, 3]:
    net_rets   = []
    gross_rets = []
    exit_tgt   = 0
    exit_stp   = 0
    exit_tm    = 0

    for row in approved:
        sig_ts    = row['signal_timestamp']
        fwd       = signal_fwd[sig_ts]

        if delay_bars == 0:
            # Immediate entry: use signal's eth_close as entry
            entry_price = row['entry_price']
            fwd_bars    = fwd
        else:
            # Delayed: enter at bar [delay_bars-1], walk remaining bars
            if len(fwd) < delay_bars:
                continue
            entry_bar   = fwd[delay_bars - 1]
            entry_price = entry_bar[1]
            fwd_bars    = fwd[delay_bars:]   # remaining bars

        if not entry_price:
            continue

        stop_price   = entry_price * (1 + FIXED_STOP_DE)
        target_price = entry_price * (1 - FIXED_TGT_DE)

        nfa_rt_pct = (0.30 / (0.1 * entry_price))
        fees_pct   = TAKER_RT + nfa_rt_pct

        exit_price  = None
        exit_reason = None

        for ts, p in fwd_bars:
            if p is None:
                continue
            if p >= stop_price:
                exit_price  = p
                exit_reason = 'stop'
                break
            if p <= target_price:
                exit_price  = p
                exit_reason = 'target'
                break
        else:
            if fwd_bars:
                exit_price  = fwd_bars[-1][1]
                exit_reason = 'time'

        if exit_price and entry_price:
            gross = (entry_price - exit_price) / entry_price * 100
            net   = gross - fees_pct * 100
            gross_rets.append(gross)
            net_rets.append(net)

            if exit_reason == 'target':
                exit_tgt += 1
            elif exit_reason == 'stop':
                exit_stp += 1
            else:
                exit_tm += 1

    n_trades = len(net_rets)
    if n_trades == 0:
        continue
    n_wins   = sum(1 for x in net_rets if x >= 0)
    wr       = n_wins / n_trades * 100
    avg_g    = avg(gross_rets)
    avg_n    = avg(net_rets)
    exp      = expectancy(net_rets)

    delay_label = f"+{delay_bars}h" if delay_bars > 0 else "immed"
    print(f"  {delay_label:>6}  "
          f"{wr:7.1f}%  "
          f"{(avg_g or 0):+9.3f}%  "
          f"{(avg_n or 0):+9.3f}%  "
          f"{(exp or 0):+11.3f}%  "
          f"{exit_tgt:4d}  {exit_stp:4d}  {exit_tm:4d}")

print("=" * 80)

# ── Final recommendation ────────────────────────────────────────
print("\n\nKEY FINDINGS:")
best = max(results_by_stop.items(), key=lambda x: x[1]['expectancy'] or -999)
print(f"  Best expectancy stop level: {best[0]*100:.2f}%  "
      f"→ {(best[1]['expectancy'] or 0):+.3f}% per trade")

first_positive = None
for stp, r in sorted(results_by_stop.items()):
    if (r['expectancy'] or -999) > 0:
        first_positive = (stp, r)
        break

if first_positive:
    print(f"  First positive expectancy:  {first_positive[0]*100:.2f}% stop")
else:
    print("  No stop level achieves positive expectancy within test range")

conn.close()
