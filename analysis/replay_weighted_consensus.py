"""
replay_weighted_consensus.py — replay historical cycles with a weighted
consensus rule replacing the current majority logic in `_final_consensus`.

Weights are derived from per-agent metrics computed inline (mirrors
forecast_quality.py's logic so this script is self-contained). Specifically:
  weight(agent) = max(MIN_WEIGHT, 0.5*hit_rate + 0.5*(1-anchoring_score))
where anchoring_score defaults to 0 when unknown.

For each post-anchor cycle, compute:
  - actual_consensus: the (grid_action, risk_action) tuple actually recorded
  - weighted_consensus: the (grid_action, risk_action) the weighted vote
    would have produced (per-axis: weighted plurality of the agent(s) voting
    on that axis — see note below)
  - action_differed: bool
  - actual_6h_pnl: observed forward 6h PnL
  - hypothetical_outcome_known: bool (always False when the weighted
    consensus differs from the actual applied action; flagged in summary)

Important: with three agents voting on disjoint axes, "weighted consensus"
on the grid axis is just Melchior's vote (scaled by his weight relative to
zero). The interesting weighted-vote effect comes from how we combine the
regime axis (Casper) with the grid axis (Melchior). We implement the
combination as: if Casper's weight × his regime-conviction > Melchior's
weight × his grid-conviction AND Casper says TRENDING, the grid_action
is overridden to RECENTRE (regime authority); otherwise Melchior's vote
stands. Documented as a simple heuristic — the goal is to detect whether
weighted reasoning would produce systematically different decisions,
not to prescribe a final algorithm.

READ-ONLY. Does not modify observer.db.
"""

import csv
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ANCHOR_FLOOR_UTC = '2026-05-19T13:04:17'
DB_PATH = '/root/xrp_grid/observer.db'
OUT_DIR = Path(__file__).resolve().parent / 'output'
AGENTS = ('casper', 'melchior', 'balthasar')
MIN_WEIGHT = 0.1

CLAMPING_OVERRIDES = {
    '[RECENTRE_COOLDOWN]', '[GRID_HEALTHY_NO_RECENTRE]',
    '[RECENT_POSITION_HOLD]', '[GRID_DEGENERATE]',
    '[NO_ACCEPTABLE_VARIANT]', '[PAUSE_INVALID]',
    '[USD_BUFFER_FLOOR]', '[XRP_BUFFER_FLOOR]',
    '[ALLOC_SKEW_CEILING]', '[DAILY_LOSS_LIMIT]',
    '[KILL_SWITCH]', '[COUNCIL_COLLAPSED]',
}


def _read_only_conn(path):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


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


def _conv(row, agent):
    try:
        return float(row.get(f'{agent}_r0_conviction') or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _position_matches_applied(agent, row):
    if agent == 'casper':
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


def derive_weights(rows):
    """Compute per-agent weights from observed hit_rate and anchoring_score."""
    weights = {}
    for agent in AGENTS:
        eligible = 0
        hits = 0
        retried_yes = 0
        retried_known = 0
        for row in rows:
            elig, match = _position_matches_applied(agent, row)
            if elig:
                eligible += 1
                if match:
                    hits += 1
            r = _freshness_retry(row, agent)
            if r is not None:
                retried_known += 1
                if r:
                    retried_yes += 1
        hit_rate = (hits / eligible) if eligible else 0.5
        anchor = (retried_yes / retried_known) if retried_known else 0.0
        raw = 0.5 * hit_rate + 0.5 * (1.0 - anchor)
        weights[agent] = max(MIN_WEIGHT, raw)
    # Normalise to sum to 1.0
    total = sum(weights.values())
    if total > 0:
        weights = {k: v / total for k, v in weights.items()}
    return weights


def weighted_grid_action(row, weights):
    """Heuristic weighted combiner for the grid axis. See module docstring."""
    casper_score = weights['casper'] * _conv(row, 'casper')
    melchior_score = weights['melchior'] * _conv(row, 'melchior')
    casper_pos = row.get('casper_r0_position')
    melchior_pos = row.get('melchior_r0_position')

    if (casper_score > melchior_score
            and casper_pos == 'TRENDING'
            and melchior_pos == 'MAINTAIN'):
        # Casper's TRENDING regime outweighs Melchior's MAINTAIN — escalate.
        return 'RECENTRE'
    if (casper_score > melchior_score
            and casper_pos == 'RANGING'
            and melchior_pos == 'RECENTRE'):
        # Casper's RANGING regime outweighs Melchior's RECENTRE — hold.
        return 'MAINTAIN'
    return melchior_pos


def weighted_risk_action(row, weights):
    """Balthasar is the only voter on the risk axis. Weight matters only to
    the extent that a very low Balthasar weight (frozen / anchoring) defaults
    to CLEAR rather than trusting the vote. Threshold at weight < MIN_WEIGHT*1.5
    (i.e., he hit floor)."""
    if weights['balthasar'] <= MIN_WEIGHT * 1.5:
        return 'CLEAR'
    return row.get('balthasar_r0_position')


def main():
    if not os.path.exists(DB_PATH):
        print(f'ERROR: {DB_PATH} not found', file=sys.stderr)
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime('%Y%m%dT%H%M%S')
    csv_path = OUT_DIR / f'weighted_consensus_replay_{timestamp}.csv'

    conn = _read_only_conn(DB_PATH)
    try:
        rows = fetch_rows(conn)
    finally:
        conn.close()

    if not rows:
        print('No post-anchor cycles found in debate_records.')
        return

    weights = derive_weights(rows)
    print(f'# Weighted consensus replay — {len(rows)} post-anchor cycles since {ANCHOR_FLOOR_UTC}')
    print(f'# Derived weights (normalised): casper={weights["casper"]:.3f} '
          f'melchior={weights["melchior"]:.3f} balthasar={weights["balthasar"]:.3f}')
    print(f'# Output: {csv_path}')
    print()

    fieldnames = ['cycle_id', 'timestamp',
                  'actual_grid_action', 'weighted_grid_action',
                  'actual_risk_action', 'weighted_risk_action',
                  'action_differed', 'actual_6h_pnl',
                  'hypothetical_outcome_known']

    differed = 0
    differed_with_pnl = []
    same_with_pnl = []

    with csv_path.open('w', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            actual_grid = row.get('final_grid_action')
            actual_risk = row.get('final_risk_action')
            w_grid = weighted_grid_action(row, weights)
            w_risk = weighted_risk_action(row, weights)
            differs = (w_grid != actual_grid) or (w_risk != actual_risk)
            if differs:
                differed += 1
            pnl_6h = row.get('pnl_6h')
            backfilled = bool(row.get('outcome_6h_backfilled'))
            # Hypothetical outcome is only "known" when actions match actual
            # — otherwise the historical PnL doesn't apply to the counterfactual.
            known = backfilled and not differs
            if backfilled and pnl_6h is not None:
                (differed_with_pnl if differs else same_with_pnl).append(float(pnl_6h))
            writer.writerow({
                'cycle_id':  row.get('cycle_id'),
                'timestamp': row.get('timestamp'),
                'actual_grid_action':   actual_grid,
                'weighted_grid_action': w_grid,
                'actual_risk_action':   actual_risk,
                'weighted_risk_action': w_risk,
                'action_differed':      int(differs),
                'actual_6h_pnl':        pnl_6h,
                'hypothetical_outcome_known': int(known),
            })

    n = len(rows)
    print(f'{"metric":<50} {"value":>10}')
    print('-' * 65)
    print(f'{"cycles_total":<50} {n:>10}')
    print(f'{"cycles_differing":<50} {differed:>10}')
    print(f'{"differing_rate":<50} {differed/n:>10.1%}')
    if same_with_pnl:
        avg_same = sum(same_with_pnl) / len(same_with_pnl)
        label = f'avg_pnl_6h_same_decision (n={len(same_with_pnl)})'
        print(f'{label:<50} {avg_same:>10.4f}')
    if differed_with_pnl:
        avg_diff = sum(differed_with_pnl) / len(differed_with_pnl)
        label = f'avg_pnl_6h_when_weighted_differs (n={len(differed_with_pnl)})'
        print(f'{label:<50} {avg_diff:>10.4f}')
        print(f'  ↑ NOTE: this is the PnL under the ACTUAL action taken, not the')
        print(f'  hypothetical. True counterfactual not knowable without re-execution.')
    print()
    print('# Interpretation:')
    print('# - differing_rate: how often weighted consensus would have made a')
    print('#   different call than the actual rule-layer-enforced consensus.')
    print('# - PnL comparisons are necessarily indirect — we can\'t know what')
    print('#   would have happened under the counterfactual action. The differing')
    print('#   rate alone is the primary signal: if it\'s near 0%, weighted')
    print('#   consensus would change nothing; if it\'s 10-30%, there is room')
    print('#   for a meaningful effect.')

    if n < 50:
        print()
        print(f'# CAVEAT: sample size {n} is small. Treat as directional, not')
        print(f'# conclusive.')


if __name__ == '__main__':
    main()
