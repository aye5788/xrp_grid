"""F5 — offline acceptance test for the 2026-06-11 five-fix rebuild.

Replays 2025-01-01 -> latest hourly bar from tape/history.db, running the
REBUILT configuration head-to-head against the OLD configuration through the
same grid simulator. The simulator mechanics are copied unchanged from the
9.5-year spacing backtest (/tmp/policy_sim.py, session 2026-06-11) that set
the 1.5% spacing floor and validated the exposure cap — F5 deliberately
reuses that model so the day-14 `matches_backtest` live check compares
against the same expectations.

Configurations (pre-committed in 02_NEXT_BUILD_TASKS.md F5; nothing fitted):
  OLD      — 0.75% spacing, no exposure cap, no stance gate. What ran before
             the rebuild.
  REBUILT  — 1.5% spacing (the documented XRP-optimal and the new floor =
             6 x maker fee; chosen exogenously, NOT swept over this window),
             exposure cap (3 linked downward rebuilds within 48h -> sells-only
             rebuild, self-releasing on a higher-centre rebuild), and the
             simulable half of the stance mandate (warehouse verdict red ->
             buy arms suppressed on rebuild, approximating STAND_ASIDE /
             PAUSE_LONGS).

Pass criteria (set before running): REBUILT must end with (a) more money AND
(b) a smaller worst drawdown than OLD over the same window.

Fidelity rules for the REBUILT config (added after the first raw run showed
10,283 rebuilds — an every-bar recentre flap the live rebuild is explicitly
designed NOT to do):
  - Rule 6 is stance-gated: while the warehouse verdict is red (the simulable
    HOLD/STAND_ASIDE proxy) a one-sided book is the mandate — NO degenerate
    rebuild fires. The book holds until the verdict clears (the "first DEPLOY
    restores the full grid" exit).
  - Council cadence: rebuild decisions happen on the daily floor + one W1 per
    breach episode (~1-3/day), so rebuilds are spaced >= 24h apart.
The OLD config keeps the raw hourly rebuild opportunity — it ran ~11
council calls/day on the T-trigger gate, and hourly evaluation is also what
the accepted 9.5y backtest modelled.

Model simplifications, applied equally to both configs: 2+2 levels, order
size ~3% of book, no taker anchor cost on rebuilds, pair-replacement after
each fill, degenerate-grid recentre at bar close. PnL is equity-based
against the $61.50 starting book (50/50).
"""
import sqlite3
import datetime
import json

DB = "/root/xrp_grid/tape/history.db"
WINDOW_START_MS = 1735689600000  # 2025-01-01 00:00 UTC

FEE = 0.0025          # Kraken tier-0 maker, both sides (matches live scorer basis)
BOOK_USD = 61.50      # real book value at the 2026-06-09 paper reset
ORDER_FRAC = 0.03     # order ~3% of book (mirrors 1.65 XRP @ ~$1.12 on $61.5)
LINK_H = 48           # exposure-cap streak linkage window (config.DOWN_WALK_LINK_HOURS)
CAP_STREAK = 3        # config.DOWN_WALK_CAP_STREAK

CONFIGS = {
    # min_rebuild_gap_h: hours between rebuild opportunities (council cadence).
    # gate_blocks_rebuild: stance-gated rule 6 — red verdict suppresses the
    # degenerate rebuild entirely (one-sided book is the mandate).
    "old": {"spacing": 0.0075, "cap": False, "gate": False,
            "min_rebuild_gap_h": 0, "gate_blocks_rebuild": False},
    "rebuilt": {"spacing": 0.015, "cap": True, "gate": True,
                "min_rebuild_gap_h": 24, "gate_blocks_rebuild": True},
    # Sensitivity bound: rebuilt WITHOUT the fidelity rules (raw policy_sim
    # mechanics — the every-bar rebuild flap the live system cannot do).
    "rebuilt_raw": {"spacing": 0.015, "cap": True, "gate": True,
                    "min_rebuild_gap_h": 0, "gate_blocks_rebuild": False},
}


def load_bars():
    conn = sqlite3.connect(DB)
    bars = conn.execute(
        """SELECT r.ts_begin, r.open, r.high, r.low, r.close,
                  COALESCE(s.verdict, 'yellow')
           FROM rollup_bars r LEFT JOIN signals_1h s ON s.ts_begin = r.ts_begin
           WHERE r.interval_min = 60 AND r.ts_begin >= ?
           ORDER BY r.ts_begin""",
        (WINDOW_START_MS,),
    ).fetchall()
    conn.close()
    return bars


def run(bars, cfg):
    spacing = cfg["spacing"]
    p0 = bars[0][4]
    usd = BOOK_USD / 2
    xrp = (BOOK_USD / 2) / p0
    size = (BOOK_USD * ORDER_FRAC) / p0  # XRP per order, fixed for the window
    realized = 0.0
    fees = 0.0
    fills = 0
    rebuilds = 0
    capped_rebuilds = 0
    streak = 0
    last_dw = None
    last_rebuild_ts = None
    peak_eq = BOOK_USD
    max_dd = 0.0
    buys, sells = [], []

    def place_grid(centre, n_buy, n_sell):
        nonlocal buys, sells
        buys, sells = [], []
        for k in range(1, n_buy + 1):
            lvl = centre * (1 - k * spacing)
            if usd >= sum(b * size for b in buys) + lvl * size:
                buys.append(lvl)
        for k in range(1, n_sell + 1):
            lvl = centre * (1 + k * spacing)
            if xrp >= (len(sells) + 1) * size:
                sells.append(lvl)
        buys.sort(reverse=True)
        sells.sort()

    def rebuild(centre, direction, ts, verdict):
        nonlocal streak, last_dw, last_rebuild_ts, rebuilds, capped_rebuilds
        # Council cadence: rebuild opportunities are spaced (Fix 4 — daily
        # floor + one W1 per breach episode), not every bar.
        if (last_rebuild_ts is not None
                and ts - last_rebuild_ts < cfg["min_rebuild_gap_h"] * 3600):
            return
        # Stance-gated rule 6 (Fix 3): under a red verdict the degenerate
        # rebuild does not fire — the one-sided book is the mandate.
        if cfg["gate_blocks_rebuild"] and verdict == "red":
            return
        rebuilds += 1
        last_rebuild_ts = ts
        n_buy, n_sell = 2, 2
        if direction < 0:
            streak = streak + 1 if (last_dw is not None and ts - last_dw <= LINK_H * 3600) else 1
            last_dw = ts
        elif direction > 0:
            streak = 0  # higher centre self-releases the cap (Fix 2)
        if cfg["cap"] and direction < 0 and streak >= CAP_STREAK:
            n_buy = 0
            capped_rebuilds += 1
        if cfg["gate"] and verdict == "red":
            n_buy = 0
        place_grid(centre, n_buy, n_sell)

    place_grid(p0, 2, 2)
    for ts_ms, o, h, l, c, verdict in bars:
        ts = ts_ms // 1000
        for lvl in [b for b in buys if l <= b]:
            if usd >= lvl * size:
                usd -= lvl * size * (1 + FEE)
                xrp += size
                fees += lvl * size * FEE
                fills += 1
                buys.remove(lvl)
                pair = lvl * (1 + spacing)
                if xrp >= (len(sells) + 1) * size:
                    sells.append(pair)
                    sells.sort()
        for lvl in [s for s in sells if h >= s]:
            if xrp >= size:
                usd += lvl * size * (1 - FEE)
                xrp -= size
                realized += lvl * size * spacing / (1 + spacing) - lvl * size * FEE
                fees += lvl * size * FEE
                fills += 1
                sells.remove(lvl)
                pair = lvl * (1 - spacing)
                if usd >= pair * size:
                    buys.append(pair)
                    buys.sort(reverse=True)
        if not buys and (not sells or c < min(sells) * (1 - 2 * spacing)):
            rebuild(c, -1, ts, verdict)
        elif not sells and (not buys or c > max(buys) * (1 + 2 * spacing)):
            rebuild(c, +1, ts, verdict)
        eq = usd + xrp * c
        if eq > peak_eq:
            peak_eq = eq
        max_dd = max(max_dd, (peak_eq - eq) / peak_eq * 100)

    cf = bars[-1][4]
    eq = usd + xrp * cf
    hodl = (BOOK_USD / 2) + (BOOK_USD / 2) * (cf / p0)
    return {
        "end_equity_usd": round(eq, 2),
        "pnl_pct": round((eq - BOOK_USD) / BOOK_USD * 100, 2),
        "hodl_pct": round((hodl - BOOK_USD) / BOOK_USD * 100, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "fills": fills,
        "fees_usd": round(fees, 2),
        "rebuilds": rebuilds,
        "capped_rebuilds": capped_rebuilds,
        "realized_usd": round(realized, 2),
    }


def main():
    bars = load_bars()
    t0 = datetime.datetime.utcfromtimestamp(bars[0][0] // 1000)
    t1 = datetime.datetime.utcfromtimestamp(bars[-1][0] // 1000)
    results = {name: run(bars, cfg) for name, cfg in CONFIGS.items()}

    old, new = results["old"], results["rebuilt"]
    crit_money = new["end_equity_usd"] > old["end_equity_usd"]
    crit_dd = new["max_drawdown_pct"] < old["max_drawdown_pct"]
    verdict = "PASS" if (crit_money and crit_dd) else "FAIL"

    report = {
        "window": {"start": str(t0), "end": str(t1), "bars": len(bars)},
        "configs": CONFIGS,
        "results": results,
        "criteria": {
            "more_money": crit_money,
            "smaller_worst_drawdown": crit_dd,
        },
        "verdict": verdict,
    }
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
