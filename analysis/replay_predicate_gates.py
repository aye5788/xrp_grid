"""
replay_predicate_gates.py — replay every post-anchor debate_records cycle
against multiple R1-trigger predicates and report what each would have fired.

Predicates evaluated:
  1. Legacy CONFLICT_MATRIX (joint-tuple incompatibility patterns from
     magi/council.py, reproduced inline so this script is self-contained).
  2. Option A — mandatory R1 minus alignment-trivial cycles (joint tuple in
     {(RANGING|UNCERTAIN, MAINTAIN, CLEAR)} with all convictions < 0.5).
  3. Option A + rule-layer-preview filter — Option A AND the historical
     hard_rule_overrides column does not contain a clamping override that
     would have made the council vote non-determinative. Approach: use the
     observed hard_rule_overrides string rather than calling production
     enforce_hard_rules (which has DB side-effects via insert_alert).
  4. Conviction-spread variants at floors 0.4 / 0.5 / 0.6 / 0.7.

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

# Clamping overrides — when any of these fire, the council's grid_action vote
# is replaced by the rule's preferred action, so R1 deliberation cannot change
# the final outcome for that cycle.
CLAMPING_OVERRIDES = {
    '[RECENTRE_COOLDOWN]',
    '[GRID_HEALTHY_NO_RECENTRE]',
    '[RECENT_POSITION_HOLD]',
    '[GRID_DEGENERATE]',
    '[NO_ACCEPTABLE_VARIANT]',
    '[PAUSE_INVALID]',
    '[USD_BUFFER_FLOOR]',
    '[XRP_BUFFER_FLOOR]',
    '[ALLOC_SKEW_CEILING]',
    '[DAILY_LOSS_LIMIT]',
    '[KILL_SWITCH]',
    '[AGENT_DEGRADED',  # prefix-match — actual override is [AGENT_DEGRADED:<id>]
    '[COUNCIL_COLLAPSED]',
}


def _read_only_conn(path):
    """Open observer.db in read-only mode so an accidental write fails loudly."""
    uri = f'file:{path}?mode=ro'
    return sqlite3.connect(uri, uri=True)


def _has_clamping_override(overrides_json):
    """Return True if the JSON-encoded list of overrides contains any tag
    that clamps the final action regardless of council vote."""
    if not overrides_json:
        return False
    try:
        tags = json.loads(overrides_json)
    except (ValueError, TypeError):
        return False
    if not isinstance(tags, list):
        return False
    for tag in tags:
        if not isinstance(tag, str):
            continue
        if tag in CLAMPING_OVERRIDES:
            return True
        # Prefix-match for AGENT_DEGRADED:<agent_id>
        if any(tag.startswith(prefix) for prefix in CLAMPING_OVERRIDES
               if prefix.endswith(':') or prefix == '[AGENT_DEGRADED'):
            return True
    return False


# ----------------------------------------------------------------------
# Predicate 1 — Legacy CONFLICT_MATRIX, reproduced inline.
# Mirror of magi/council.py:CONFLICT_MATRIX as of 2026-05-21.
# ----------------------------------------------------------------------

def _conv(row, agent):
    try:
        return float(row.get(f'{agent}_r0_conviction') or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _match_wild(pattern, value):
    return pattern == '*' or pattern == value


def legacy_predicate(row):
    """Return True if any legacy CONFLICT_MATRIX rule would fire for this
    cycle. The grid-state-aware rules (one-sided MAINTAIN, stale MAINTAIN,
    PAUSE_LONGS-on-empty-buys, etc.) require open_orders state we did not
    snapshot per-cycle, so they are evaluated *only* via the action-
    incompatibility patterns reproduced here. Documented limitation."""
    c = row.get('casper_r0_position')
    m = row.get('melchior_r0_position')
    b = row.get('balthasar_r0_position')

    # Rule: (TRENDING, TIGHTEN, *)
    if _match_wild('TRENDING', c) and _match_wild('TIGHTEN', m):
        return True
    # Rule: (*, WIDEN, PAUSE_LONGS)
    if _match_wild('WIDEN', m) and _match_wild('PAUSE_LONGS', b):
        return True
    # Rule: (*, WIDEN, PAUSE_SHORTS)
    if _match_wild('WIDEN', m) and _match_wild('PAUSE_SHORTS', b):
        return True
    # Rule: (*, *, HALT) + bal_conv > 0.6
    if b == 'HALT' and _conv(row, 'balthasar') > 0.6:
        return True
    return False


# ----------------------------------------------------------------------
# Predicate 2 — Option A (mandatory minus alignment-trivial).
# ----------------------------------------------------------------------

ALIGNMENT_TRIVIAL_REGIME = {'RANGING', 'UNCERTAIN'}


def option_a_predicate(row, conviction_floor_for_trivial=0.5):
    """Fires unless the joint tuple is alignment-trivial AND all three
    convictions are below the floor."""
    c = row.get('casper_r0_position')
    m = row.get('melchior_r0_position')
    b = row.get('balthasar_r0_position')
    triv = (c in ALIGNMENT_TRIVIAL_REGIME
            and m == 'MAINTAIN'
            and b == 'CLEAR'
            and _conv(row, 'casper')    < conviction_floor_for_trivial
            and _conv(row, 'melchior')  < conviction_floor_for_trivial
            and _conv(row, 'balthasar') < conviction_floor_for_trivial)
    return not triv


def option_a_preview_predicate(row):
    """Option A AND the historical hard_rule_overrides column does NOT
    contain a clamping override. If a clamp is recorded, the council vote
    was non-determinative for that cycle, so R1 deliberation would have
    added cost without affecting the action."""
    if not option_a_predicate(row):
        return False
    if _has_clamping_override(row.get('hard_rule_overrides')):
        return False
    return True


# ----------------------------------------------------------------------
# Predicate 3 — Conviction-spread variants.
# ----------------------------------------------------------------------

def conviction_spread_predicate(row, floor):
    """Fires when at least two agents are at conviction >= floor AND the
    positions are not pairwise identical (always true here since the three
    agents vote on disjoint vocabularies — so the conviction floor is the
    actual gate; the "differ" clause is informational)."""
    agents = ('casper', 'melchior', 'balthasar')
    confident = [a for a in agents if _conv(row, a) >= floor]
    return len(confident) >= 2


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def fetch_rows(conn):
    cur = conn.cursor()
    cur.execute(
        '''SELECT * FROM debate_records
           WHERE timestamp >= ?
           ORDER BY id ASC''',
        (ANCHOR_FLOOR_UTC,),
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return rows


def main():
    if not os.path.exists(DB_PATH):
        print(f'ERROR: {DB_PATH} not found', file=sys.stderr)
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime('%Y%m%dT%H%M%S')
    csv_path = OUT_DIR / f'predicate_gates_{timestamp}.csv'

    conn = _read_only_conn(DB_PATH)
    try:
        rows = fetch_rows(conn)
    finally:
        conn.close()

    if not rows:
        print('No post-anchor cycles found in debate_records.')
        return

    spread_floors = (0.4, 0.5, 0.6, 0.7)
    fieldnames = ['cycle_id', 'timestamp',
                  'would_fire_legacy',
                  'would_fire_optA',
                  'would_fire_optA_preview']
    fieldnames += [f'would_fire_spread_{f}' for f in spread_floors]

    tallies = {name: 0 for name in fieldnames if name.startswith('would_fire_')}

    with csv_path.open('w', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            verdicts = {
                'would_fire_legacy':        legacy_predicate(row),
                'would_fire_optA':          option_a_predicate(row),
                'would_fire_optA_preview':  option_a_preview_predicate(row),
            }
            for f in spread_floors:
                verdicts[f'would_fire_spread_{f}'] = conviction_spread_predicate(row, f)
            for k, v in verdicts.items():
                if v:
                    tallies[k] += 1
            writer.writerow({
                'cycle_id':  row.get('cycle_id'),
                'timestamp': row.get('timestamp'),
                **{k: int(bool(v)) for k, v in verdicts.items()},
            })

    n = len(rows)
    print(f'# Predicate gate replay — {n} post-anchor cycles since {ANCHOR_FLOOR_UTC}')
    print(f'# Output: {csv_path}')
    print()
    print(f'{"predicate":<35} {"fires":>8} {"rate":>8}')
    print('-' * 55)
    for name in fieldnames:
        if not name.startswith('would_fire_'):
            continue
        cnt = tallies[name]
        rate = cnt / n if n else 0.0
        print(f'{name:<35} {cnt:>8} {rate:>8.1%}')

    # Print observed actual debate_triggered rate for comparison
    actual = sum(1 for r in rows if (r.get('debate_triggered') or 0))
    print()
    print(f'# Reference: actual debate_triggered in this window = {actual}/{n} ({actual/n:.1%})')

    # Sample-size caveat
    if n < 50:
        print()
        print(f'# CAVEAT: sample size {n} is small. Trigger rates are directional,')
        print(f'# not statistically reliable. Re-run after more post-anchor cycles')
        print(f'# accumulate.')


if __name__ == '__main__':
    main()
