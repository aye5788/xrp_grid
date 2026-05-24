"""
forecast_quality.py — per-agent forecast quality + behavioural metrics
over the post-anchor `debate_records` window.

For each of casper / melchior / balthasar, compute:
  - corr_conv_pnl_6h: Pearson correlation of R0 conviction vs forward
    6h PnL, restricted to cycles where the agent's vote was load-bearing
    (i.e. the cycle did NOT trip a clamping hard-rule override).
  - hit_rate: fraction of load-bearing cycles where the agent's R0
    position aligns with the action the rule layer eventually produced
    (mapped per-agent: casper→regime axis, melchior→grid axis,
    balthasar→risk axis).
  - distinct_vote_rate: fraction of cycles where this agent's position
    differs from the modal position among the three. Approximated by
    "this agent is unique in its own axis" — since vocabularies are
    disjoint, each agent is always unique by axis; the actual signal we
    want is *changed position relative to the prior cycle*. Both reported.
  - anchoring_score: fraction of cycles where the freshness validator
    forced a retry for this agent (from `debate_records.freshness_retries`,
    introduced 2026-05-21 — historical rows are NULL, exclude from the
    denominator).

READ-ONLY. Does not modify observer.db.
"""

import csv
import json
import math
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ANCHOR_FLOOR_UTC = '2026-05-19T13:04:17'
DB_PATH = '/root/xrp_grid/observer.db'
OUT_DIR = Path(__file__).resolve().parent / 'output'
AGENTS = ('casper', 'melchior', 'balthasar')

CLAMPING_OVERRIDES = {
    '[RECENTRE_COOLDOWN]', '[GRID_HEALTHY_NO_RECENTRE]',
    '[RECENT_POSITION_HOLD]', '[GRID_DEGENERATE]',
    '[NO_ACCEPTABLE_VARIANT]', '[PAUSE_INVALID]',
    '[USD_BUFFER_FLOOR]', '[XRP_BUFFER_FLOOR]',
    '[ALLOC_SKEW_CEILING]', '[DAILY_LOSS_LIMIT]',
    '[KILL_SWITCH]', '[COUNCIL_COLLAPSED]',
}


def _read_only_conn(path):
    uri = f'file:{path}?mode=ro'
    return sqlite3.connect(uri, uri=True)


def _has_clamping_override(overrides_json):
    if not overrides_json:
        return False
    try:
        tags = json.loads(overrides_json)
    except (ValueError, TypeError):
        return False
    if not isinstance(tags, list):
        return False
    for tag in tags:
        if isinstance(tag, str) and (
            tag in CLAMPING_OVERRIDES or tag.startswith('[AGENT_DEGRADED')
        ):
            return True
    return False


def _pearson(xs, ys):
    """Pearson correlation. Returns None if degenerate (n<2 or zero variance)."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def _conv(row, agent):
    try:
        return float(row.get(f'{agent}_r0_conviction') or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _position_matches_applied(agent, row):
    """Map agent vote axis -> final/applied action axis. Returns
    (eligible_for_hit_rate, matched) tuple."""
    if agent == 'casper':
        # Casper votes regime; rule layer produces grid_action. We approximate
        # 'match' by checking whether his regime call is consistent with the
        # final grid_action: TRENDING/UNCERTAIN → RECENTRE-favouring;
        # RANGING → MAINTAIN-favouring.
        regime = row.get('casper_r0_position')
        final = row.get('final_grid_action')
        if regime is None or final is None:
            return (False, False)
        if regime == 'RANGING' and final == 'MAINTAIN':
            return (True, True)
        if regime in ('TRENDING', 'UNCERTAIN') and final in ('RECENTRE', 'TIGHTEN', 'WIDEN'):
            return (True, True)
        return (True, False)
    if agent == 'melchior':
        # Melchior votes grid_action directly. Match = his vote equals the
        # applied (post-engine-clamp) action.
        vote = row.get('melchior_r0_position')
        applied = row.get('applied_grid_action') or row.get('final_grid_action')
        if vote is None or applied is None:
            return (False, False)
        return (True, vote == applied)
    if agent == 'balthasar':
        vote = row.get('balthasar_r0_position')
        final_risk = row.get('final_risk_action')
        if vote is None or final_risk is None:
            return (False, False)
        return (True, vote == final_risk)
    return (False, False)


def _freshness_retry(row, agent):
    """Return True/False/None. None = column unpopulated (pre-2026-05-21)."""
    raw = row.get('freshness_retries')
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict):
        return None
    return bool(d.get(agent))


def fetch_rows(conn):
    cur = conn.cursor()
    cur.execute(
        '''SELECT * FROM debate_records
           WHERE timestamp >= ?
           ORDER BY id ASC''',
        (ANCHOR_FLOOR_UTC,),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def analyse_agent(agent, rows):
    """Return a dict of per-agent metrics."""
    convs = []
    pnls = []
    hits = 0
    eligible = 0
    distinct_axis = 0
    distinct_axis_eligible = 0
    retries_yes = 0
    retries_known = 0

    prev_position = None
    for row in rows:
        # Forecast quality — only load-bearing cycles
        load_bearing = not _has_clamping_override(row.get('hard_rule_overrides'))
        if (load_bearing
                and row.get('outcome_6h_backfilled')
                and row.get('pnl_6h') is not None):
            convs.append(_conv(row, agent))
            try:
                pnls.append(float(row.get('pnl_6h') or 0.0))
            except (TypeError, ValueError):
                pnls.pop()  # roll back the conviction append

        # Hit rate
        elig, match = _position_matches_applied(agent, row)
        if elig:
            eligible += 1
            if match:
                hits += 1

        # Distinct-from-prior (change-detection on this agent's own axis)
        pos = row.get(f'{agent}_r0_position')
        if pos is not None:
            distinct_axis_eligible += 1
            if prev_position is not None and pos != prev_position:
                distinct_axis += 1
            prev_position = pos

        # Freshness retry
        retried = _freshness_retry(row, agent)
        if retried is not None:
            retries_known += 1
            if retried:
                retries_yes += 1

    corr = _pearson(convs, pnls)
    return {
        'agent': agent,
        'corr_conv_pnl_6h': corr,
        'corr_sample_n': len(convs),
        'hit_rate': (hits / eligible) if eligible else None,
        'hit_rate_n': eligible,
        'distinct_from_prior_rate': (distinct_axis / distinct_axis_eligible)
            if distinct_axis_eligible else None,
        'distinct_n': distinct_axis_eligible,
        'anchoring_score': (retries_yes / retries_known) if retries_known else None,
        'anchoring_known_n': retries_known,
    }


def main():
    if not os.path.exists(DB_PATH):
        print(f'ERROR: {DB_PATH} not found', file=sys.stderr)
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime('%Y%m%dT%H%M%S')
    csv_path = OUT_DIR / f'forecast_quality_{timestamp}.csv'

    conn = _read_only_conn(DB_PATH)
    try:
        rows = fetch_rows(conn)
    finally:
        conn.close()

    if not rows:
        print('No post-anchor cycles found in debate_records.')
        return

    results = [analyse_agent(a, rows) for a in AGENTS]

    fieldnames = ['agent', 'corr_conv_pnl_6h', 'corr_sample_n',
                  'hit_rate', 'hit_rate_n',
                  'distinct_from_prior_rate', 'distinct_n',
                  'anchoring_score', 'anchoring_known_n']
    with csv_path.open('w', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow(r)

    n = len(rows)
    print(f'# Per-agent forecast quality — {n} post-anchor cycles since {ANCHOR_FLOOR_UTC}')
    print(f'# Output: {csv_path}')
    print()
    print(f'{"agent":<11} {"corr(conv,pnl_6h)":>18} {"n":>4}   '
          f'{"hit_rate":>9} {"n":>4}   {"distinct/prior":>15} {"anchor_rate":>11} {"n":>4}')
    print('-' * 105)

    def _fmt(v, spec):
        return spec.format(v) if v is not None else 'n/a'

    for r in results:
        print(f'{r["agent"]:<11} '
              f'{_fmt(r["corr_conv_pnl_6h"], "{:>18.3f}"):>18} '
              f'{r["corr_sample_n"]:>4}   '
              f'{_fmt(r["hit_rate"], "{:>9.1%}"):>9} '
              f'{r["hit_rate_n"]:>4}   '
              f'{_fmt(r["distinct_from_prior_rate"], "{:>15.1%}"):>15} '
              f'{_fmt(r["anchoring_score"], "{:>11.1%}"):>11} '
              f'{r["anchoring_known_n"]:>4}')

    print()
    print('# Interpretation:')
    print('# - corr(conv, pnl_6h): if positive, the agent\'s confidence correlates')
    print('#   with subsequent PnL on load-bearing cycles. Near zero or negative =')
    print('#   conviction is not predictive of outcomes.')
    print('# - hit_rate: fraction of cycles where the agent\'s vote matched the')
    print('#   action that actually got applied. Per-axis mapping: casper→regime↔')
    print('#   grid, melchior→grid, balthasar→risk.')
    print('# - distinct/prior: fraction of cycles where the agent changed position')
    print('#   vs the prior cycle. Frozen agents will be near 0%.')
    print('# - anchor_rate: from freshness_retries (only populated since')
    print('#   2026-05-21). Higher = more anchoring caught by the L3 validator.')

    if n < 50:
        print()
        print(f'# CAVEAT: sample size {n} is small. Correlations are directional,')
        print(f'# not significant. Re-run after more cycles accumulate.')


if __name__ == '__main__':
    main()
