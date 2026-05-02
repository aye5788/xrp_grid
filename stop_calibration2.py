#!/usr/bin/env python3
# =============================================================
# STOP CALIBRATION — EXTENDED TIME WINDOW WITH DELAYED ENTRY
# Tests +3h delayed entry (eliminates 15/17 initial stop outs)
# across multiple time windows and stop levels to find a
# viable parameter set.
# Also shows the raw forward price path distribution.
# =============================================================

import sys, os
sys.path.insert(0, '/root/eth_observer')
os.chdir('/root/eth_observer')

import sqlite3
import statistics

DB_PATH = '/root/eth_observer/observer.db'
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

approved = conn.execute("""
    SELECT signal_timestamp, entry_price, price_4h_return,
           short_liq_usd, short_long_ratio, fear_greed_at_signal,
           melchior_conviction
    FROM magi_backtest_decisions
    WHERE consensus_result = 'short'
    ORDER BY signal_timestamp
""").fetchall()

print(f"MAGI-approved short signals: {len(approved)}")

TAKER_RT = 0.0018   # round-trip taker rate

def avg(lst):
    clean = [x for x in lst if x is not None]
    return statistics.mean(clean) if clean else None

def expectancy(net_list):
    if not net_list:
        return None
    w = [x for x in net_list if x >= 0]
    l = [x for x in net_list if x < 0]
    wr = len(w) / len(net_list)
    return (wr * (avg(w) or 0)) + ((1 - wr) * (avg(l) or 0))

# Pre-load 48 forward bars per signal
print("Loading 48h forward bars...")
signal_fwd = {}
for row in approved:
    sig_ts   = row['signal_timestamp']
    fwd_rows = conn.execute("""
        SELECT timestamp, eth_close FROM backtest_results
        WHERE timestamp > ? ORDER BY timestamp ASC LIMIT 48
    """, (sig_ts,)).fetchall()
    signal_fwd[sig_ts] = [(r['timestamp'], r['eth_close']) for r in fwd_rows]

# ── 1. Raw forward price distribution from signal close ─────────
print("\n\n1. FORWARD PRICE DISTRIBUTION (from signal entry, immediate)")
print("   Shows % of signals where price touched each level at any point\n")

check_levels = [-0.005, -0.010, -0.015, -0.020, -0.025, -0.030]
adverse_levels = [+0.005, +0.010, +0.015, +0.020]

print(f"   {'Horizon':>8}", end="")
for lv in check_levels:
    print(f"  {lv*100:+.1f}%", end="")
for lv in adverse_levels:
    print(f"  {lv*100:+.1f}%", end="")
print()
print("   " + "-" * 75)

for horizon in [4, 8, 12, 24, 36, 48]:
    row_line = f"   {horizon:>6}h  "
    for lv in check_levels + adverse_levels:
        touched = 0
        total   = 0
        for row in approved:
            sig_ts = row['signal_timestamp']
            entry  = row['entry_price']
            if not entry:
                continue
            fwd    = signal_fwd[sig_ts][:horizon]
            total += 1
            threshold = entry * (1 + lv)
            for _, p in fwd:
                if p is None:
                    continue
                if lv < 0 and p <= threshold:
                    touched += 1
                    break
                elif lv > 0 and p >= threshold:
                    touched += 1
                    break
        row_line += f"  {touched/total*100:4.0f}%" if total else "   N/A"
    print(row_line)

print()

# ── 2. Delay + extended window grid ────────────────────────────
print("\n2. DELAY × TIME-WINDOW GRID")
print("   Stop 1.50%, Target 2.0%")
print("   (Delay = hours after signal before entry; Window = max hours to hold)\n")

STOP_PCT = 0.0150
TGT_PCT  = 0.0200

delays  = [0, 1, 2, 3, 4]
windows = [9, 12, 18, 24, 36]

header = f"   {'Delay/Window':>14}"
for w in windows:
    header += f"  {w:>4}h"
print(header)
print("   " + "-" * (14 + len(windows) * 7))

for delay in delays:
    row_str = f"   {'+'+str(delay)+'h delay':>14}"
    for window in windows:
        net_rets = []
        for row in approved:
            sig_ts = row['signal_timestamp']
            fwd    = signal_fwd[sig_ts]

            if delay == 0:
                entry_price = row['entry_price']
                trade_bars  = fwd[:window]
            else:
                if len(fwd) < delay:
                    continue
                entry_price = fwd[delay - 1][1]
                trade_bars  = fwd[delay:delay + window]

            if not entry_price:
                continue

            stop_price   = entry_price * (1 + STOP_PCT)
            target_price = entry_price * (1 - TGT_PCT)
            nfa_rt_pct   = 0.30 / (0.1 * entry_price)
            fees          = (TAKER_RT + nfa_rt_pct) * 100

            exit_price = None
            for _, p in trade_bars:
                if p is None:
                    continue
                if p >= stop_price:
                    exit_price = p
                    break
                if p <= target_price:
                    exit_price = p
                    break
            else:
                if trade_bars:
                    exit_price = trade_bars[-1][1]

            if exit_price:
                gross = (entry_price - exit_price) / entry_price * 100
                net   = gross - fees
                net_rets.append(net)

        if not net_rets:
            row_str += f"  {'N/A':>5}"
        else:
            exp = expectancy(net_rets)
            exp_str = f"{exp:+.2f}%"
            row_str += f"  {exp_str:>6}"

    print(row_str)

print()

# ── 3. Best overall configuration deep-dive ─────────────────────
print("\n3. DEEP DIVE: Best overall — +3h delay, 36h window")
print("   Stop 1.50%, Target 2.0%\n")

DELAY    = 3
WINDOW   = 36
STOP_PCT = 0.0150
TGT_PCT  = 0.0200

detail_rows = []

for row in approved:
    sig_ts = row['signal_timestamp']
    fwd    = signal_fwd[sig_ts]

    if len(fwd) < DELAY:
        continue
    entry_price = fwd[DELAY - 1][1]
    trade_bars  = fwd[DELAY:DELAY + WINDOW]

    if not entry_price:
        continue

    stop_price   = entry_price * (1 + STOP_PCT)
    target_price = entry_price * (1 - TGT_PCT)
    nfa_rt_pct   = 0.30 / (0.1 * entry_price)
    fees          = (TAKER_RT + nfa_rt_pct) * 100

    exit_price  = None
    exit_reason = 'time'
    exit_h      = len(trade_bars)

    for hi, (_, p) in enumerate(trade_bars):
        if p is None:
            continue
        if p >= stop_price:
            exit_price  = p
            exit_reason = 'stop'
            exit_h      = hi + 1
            break
        if p <= target_price:
            exit_price  = p
            exit_reason = 'target'
            exit_h      = hi + 1
            break
    else:
        if trade_bars:
            exit_price = trade_bars[-1][1]

    if exit_price:
        gross = (entry_price - exit_price) / entry_price * 100
        net   = gross - fees
        detail_rows.append({
            'ts':         sig_ts,
            'entry':      entry_price,
            'exit':       exit_price,
            'gross':      gross,
            'net':        net,
            'exit_r':     exit_reason,
            'exit_h':     exit_h,
            'fg':         row['fear_greed_at_signal'],
            'conv':       row['melchior_conviction'],
            'ratio':      row['short_long_ratio'],
            'short_liq':  row['short_liq_usd'],
        })

n    = len(detail_rows)
wins = [r for r in detail_rows if r['net'] >= 0]
n_wins = len(wins)
wr   = n_wins / n * 100 if n else 0

all_net   = [r['net'] for r in detail_rows]
wins_net  = [r['net'] for r in wins]
loss_net  = [r['net'] for r in detail_rows if r['net'] < 0]

tgt_hits = sum(1 for r in detail_rows if r['exit_r'] == 'target')
stp_hits = sum(1 for r in detail_rows if r['exit_r'] == 'stop')
tm_exits = sum(1 for r in detail_rows if r['exit_r'] == 'time')

print(f"   Trades:        {n}")
print(f"   Win rate:      {wr:.1f}%  ({n_wins}W / {n-n_wins}L)")
print(f"   Avg net:       {avg(all_net):+.3f}%")
print(f"   Avg net (W):   {avg(wins_net):+.3f}%")
print(f"   Avg net (L):   {avg(loss_net):+.3f}%")
print(f"   Expectancy:    {expectancy(all_net):+.3f}%")
print(f"   Exit breakdown: {tgt_hits} target | {stp_hits} stop | {tm_exits} time")

print(f"\n   {'Timestamp':<22} {'Entry':>7} {'Exit':>7} "
      f"{'Gross':>8} {'Net':>8} {'Exit':>8} {'F&G':>4} {'Ratio':>6}")
print("   " + "-" * 82)
for r in sorted(detail_rows, key=lambda x: x['net']):
    print(f"   {r['ts']:<22} "
          f"${r['entry']:>6.0f} ${r['exit']:>6.0f} "
          f"{r['gross']:>+7.2f}% {r['net']:>+7.2f}% "
          f"{'['+r['exit_r']+']':>8} {r['fg'] or '--':>4} "
          f"{r['ratio']:>5.2f}x")

# ── 4. F&G filter test ─────────────────────────────────────────
print("\n\n4. F&G FILTER: exclude F&G > 45 (moderate fear only)")
print("   +3h delay, 36h window, stop 1.50%, target 2.0%\n")

fg_filtered = [r for r in detail_rows if r['fg'] is not None and r['fg'] <= 45]
fg_all      = detail_rows

for label, group in [("All signals", fg_all), ("F&G ≤ 45 only", fg_filtered)]:
    n     = len(group)
    wins  = sum(1 for r in group if r['net'] >= 0)
    nets  = [r['net'] for r in group]
    wr    = wins / n * 100 if n else 0
    print(f"   {label}: n={n}, WR={wr:.0f}%, "
          f"avg={avg(nets):+.3f}%, exp={expectancy(nets):+.3f}%")

conn.close()
