"""
oneshot_doctrine.py — single Anthropic API call to test doctrine generation
viability. Reads the last 30 cycles of debate_records + their outcome
backfills, sends a structured analysis prompt to Claude Haiku 4.5, saves the
raw output to analysis/output/doctrine_generation_<timestamp>.md.

Cost budget: max 25k input tokens, max 2k output. Target cost < $0.05.

READ-ONLY on observer.db. Makes exactly one LLM call.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = str(REPO_ROOT / 'observer.db')
OUT_DIR = Path(__file__).resolve().parent / 'output'
MODEL_ID = 'claude-haiku-4-5'
MAX_INPUT_TOKENS_BUDGET = 25_000
MAX_OUTPUT_TOKENS = 2_000
LAST_N_CYCLES = 30


def _load_env():
    """Load .env so ANTHROPIC_API_KEY is present."""
    env_path = REPO_ROOT / '.env'
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def _read_only_conn(path):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


def fetch_last_n(conn, n):
    cur = conn.cursor()
    cur.execute(
        '''SELECT cycle_id, timestamp, trigger,
                  casper_r0_position,    casper_r0_conviction,    casper_r0_crux,
                  melchior_r0_position,  melchior_r0_conviction,  melchior_r0_crux,
                  balthasar_r0_position, balthasar_r0_conviction, balthasar_r0_crux,
                  final_grid_action, final_risk_action,
                  hard_rule_overrides, geometry_source,
                  fills_1h, fills_6h, pnl_6h, skew_delta_6h, grid_alive_6h,
                  outcome_6h_backfilled
           FROM debate_records
           ORDER BY id DESC
           LIMIT ?''',
        (n,),
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    rows.reverse()  # chronological order in the prompt
    return rows


def build_compact_summary(rows):
    """Turn the cycle rows into a compact text block. We pack into a
    tabular format to keep token count down."""
    lines = []
    lines.append('CYCLE_DATA — last {} cycles, chronological.'.format(len(rows)))
    lines.append('format: ts | C={regime}/{conv} M={action}/{conv} B={risk}/{conv} '
                 '→ final=grid/{action} risk/{action} '
                 '| overrides=[...] | 6h: fills={n} pnl={x} alive={0/1}')
    lines.append('')
    for r in rows:
        ovr = r.get('hard_rule_overrides') or '[]'
        # strip JSON brackets and quotes for compactness
        try:
            ovr_list = json.loads(ovr)
            ovr_compact = ','.join(t.strip('[]') for t in ovr_list) if ovr_list else '-'
        except (ValueError, TypeError):
            ovr_compact = '-'
        backfilled = r.get('outcome_6h_backfilled')
        pnl = r.get('pnl_6h')
        fills = r.get('fills_6h')
        alive = r.get('grid_alive_6h')
        outcome_str = (f'fills={fills} pnl={pnl} alive={alive}'
                       if backfilled else 'outcome=pending')
        line = (
            f'{r["timestamp"][:16]} | '
            f'C={r.get("casper_r0_position"):>9}/{(r.get("casper_r0_conviction") or 0):.2f} '
            f'M={r.get("melchior_r0_position"):>9}/{(r.get("melchior_r0_conviction") or 0):.2f} '
            f'B={r.get("balthasar_r0_position"):>13}/{(r.get("balthasar_r0_conviction") or 0):.2f} '
            f'→ {r.get("final_grid_action"):>9}/{r.get("final_risk_action"):>13} '
            f'| ovr={ovr_compact:<35} | 6h: {outcome_str}'
        )
        lines.append(line)
    return '\n'.join(lines)


SYSTEM_PROMPT = """You are an analyst reviewing trading-system telemetry. \
You produce concise, evidence-grounded doctrine proposals for an adaptive \
grid-trading bot. Your audience is a single technical operator who will \
manually evaluate and merge your proposals. Be specific. Cite cycle \
timestamps and override tags. Do not invent fields not present in the data."""

USER_PROMPT_TEMPLATE = """Below is the last {n} cycles of trading-system \
telemetry from an XRP/USD grid bot. Three LLM agents (Casper=regime, \
Melchior=grid_action, Balthasar=risk_action) vote each cycle. A hard-rule \
layer can override them; the `overrides` field lists which bracketed rule \
tags fired. 6h-forward outcomes are backfilled where available.

Existing hard-rule tag glossary (partial):
- [RECENTRE_COOLDOWN]: blocks RECENTRE if rebuild happened <60min ago
- [GRID_HEALTHY_NO_RECENTRE]: blocks RECENTRE when book is bilateral and price near centre
- [RECENT_POSITION_HOLD]: blocks rebuild when an open round-trip might close naturally
- [GRID_DEGENERATE]: forces RECENTRE when book is one-sided
- [GEOMETRY_INJECTED_FROM_SCORER]: scorer fallback for null Melchior geometry
- [NO_ACCEPTABLE_VARIANT]: forces GRID_PAUSE when no scorer variant clears the per-level positivity hard requirement

Your task — produce three sections:

## 1. Override frequency analysis
List each override tag that fired across the window, with count and a one-line observation on the situations triggering it. Cite specific cycle timestamps as examples.

## 2. Proposed rule modifications
For each proposal, give:
- TARGET: which existing rule (or 'NEW RULE' for additions)
- PREDICATE: the specific condition (in pseudocode or English, citing existing world_state fields)
- RATIONALE: which cycles in the data support the change
- CONFIDENCE: HIGH / MEDIUM / LOW, with one sentence on what evidence would raise it

Limit yourself to at most 3 proposals. Prefer fewer, well-grounded changes over many speculative ones.

## 3. What you cannot tell from this data
Be explicit about gaps — e.g., outcomes not yet backfilled, missing world_state fields you would want to see, sample-size limitations.

----- BEGIN CYCLE_DATA -----
{data}
----- END CYCLE_DATA -----
"""


def main():
    _load_env()
    if 'ANTHROPIC_API_KEY' not in os.environ:
        print('ERROR: ANTHROPIC_API_KEY not in environment (or .env)', file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(DB_PATH):
        print(f'ERROR: {DB_PATH} not found', file=sys.stderr)
        sys.exit(1)

    try:
        import anthropic
    except ImportError:
        print('ERROR: anthropic package not installed. Install with:', file=sys.stderr)
        print('  pip install anthropic', file=sys.stderr)
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime('%Y%m%dT%H%M%S')
    out_path = OUT_DIR / f'doctrine_generation_{timestamp}.md'

    conn = _read_only_conn(DB_PATH)
    try:
        rows = fetch_last_n(conn, LAST_N_CYCLES)
    finally:
        conn.close()

    if not rows:
        print('No debate_records rows found — nothing to analyse.', file=sys.stderr)
        sys.exit(1)

    cycle_data = build_compact_summary(rows)
    user_prompt = USER_PROMPT_TEMPLATE.format(n=len(rows), data=cycle_data)

    # Quick conservative input-token estimate. We pass actual control to the
    # API; this is just a guardrail so we fail loudly if the prompt blows out.
    approx_input_chars = len(SYSTEM_PROMPT) + len(user_prompt)
    approx_input_tokens = approx_input_chars // 4
    if approx_input_tokens > MAX_INPUT_TOKENS_BUDGET:
        print(f'ERROR: approx input tokens ({approx_input_tokens}) > budget '
              f'({MAX_INPUT_TOKENS_BUDGET}). Reduce LAST_N_CYCLES or compact further.',
              file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic()

    print(f'# Doctrine generation — Haiku 4.5 single-call test')
    print(f'# Cycles in prompt: {len(rows)}')
    print(f'# Approx input chars: {approx_input_chars} (~{approx_input_tokens} tokens)')
    print(f'# Model: {MODEL_ID}')
    print(f'# Output: {out_path}')
    print('# Calling Anthropic API...')

    try:
        resp = client.messages.create(
            model=MODEL_ID,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
    except Exception as e:
        print(f'ERROR: Anthropic API call failed: {e!r}', file=sys.stderr)
        sys.exit(2)

    text_blocks = [b.text for b in resp.content if getattr(b, 'type', None) == 'text']
    body = '\n\n'.join(text_blocks)

    usage = getattr(resp, 'usage', None)
    in_tok = getattr(usage, 'input_tokens', None) if usage else None
    out_tok = getattr(usage, 'output_tokens', None) if usage else None

    md = []
    md.append(f'# Doctrine generation — {MODEL_ID}')
    md.append(f'Generated: {timestamp} UTC')
    md.append(f'Cycles analysed: {len(rows)} (last LAST_N_CYCLES from debate_records)')
    if in_tok is not None and out_tok is not None:
        # Haiku 4.5 pricing: ~$1/M input, ~$5/M output
        cost = (in_tok / 1_000_000) * 1.00 + (out_tok / 1_000_000) * 5.00
        md.append(f'Usage: input_tokens={in_tok}, output_tokens={out_tok}, '
                  f'approx_cost=${cost:.4f}')
    md.append('')
    md.append('---')
    md.append('')
    md.append(body)
    out_path.write_text('\n'.join(md))

    print(f'# Done. Output saved to {out_path}')
    if in_tok is not None and out_tok is not None:
        cost = (in_tok / 1_000_000) * 1.00 + (out_tok / 1_000_000) * 5.00
        print(f'# Usage: input={in_tok}, output={out_tok}, approx_cost=${cost:.4f}')


if __name__ == '__main__':
    main()
