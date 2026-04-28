# =============================================================
# BALTHASAR TEST HARNESS
# Tests Balthasar with three scenarios:
# 1. Clean setup — should approve
# 2. Hard veto — spread too wide
# 3. Live data — real account + real market data
# Run from ~/eth_observer with venv active:
#   python -m magi.test_balthasar
# =============================================================

import os
import sys
sys.path.insert(0, os.path.expanduser("~/eth_observer"))

from magi.balthasar import (
    Balthasar,
    compute_time_analysis,
    compute_account_analysis,
    compute_friction_analysis,
    compute_drawdown_analysis,
    get_account_health,
    get_24h_rows,
    get_recent_signal_history,
)


def print_vote(vote: dict):
    print(f"  Vote:        {vote['vote'].upper()}")
    print(f"  Conviction:  {vote['conviction']}")
    print(f"  Veto:        {vote['veto']}")
    if vote.get('veto_reason'):
        print(f"  Veto reason: {vote['veto_reason']}")
    print(f"  Status:      {vote['status']}")
    print(f"\n  Reasoning:")
    print(f"  {vote['reasoning']}")
    print(f"\n  Concerns:")
    for c in vote['concerns']:
        print(f"  - {c}")


def run():
    print("=" * 65)
    print("BALTHASAR-2 TEST HARNESS")
    print("=" * 65)

    balthasar = Balthasar()

    # ── Test 1: Pre-computation checks ────────────────────────
    print("\n--- PRE-COMPUTATION VALIDATION ---")

    print("\nTime analysis:")
    time_anal = compute_time_analysis()
    for k, v in time_anal.items():
        print(f"  {k}: {v}")

    print("\nAccount health (live from Coinbase):")
    acct = get_account_health()
    print(f"  API status: {acct.get('api_status')}")
    if acct.get('api_status') == 'ok':
        print(f"  Buying power: ${acct.get('futures_buying_power', 0):.2f}")
        print(f"  Liq buffer:   {acct.get('liquidation_buffer_pct', 0):.0f}%")
        print(f"  Margin window:{acct.get('margin_window_type')}")

    acct_anal = compute_account_analysis(acct)
    print(f"\nAccount analysis status: {acct_anal['status']}")

    print("\nFriction analysis (at current spread ~0.034%):")
    friction = compute_friction_analysis(0.034, 2316.0)
    for k, v in friction.items():
        print(f"  {k}: {v}")

    print("\nSignal history:")
    sig_hist = get_recent_signal_history()
    print(f"  Completed signals: {len(sig_hist)}")

    drawdown = compute_drawdown_analysis(
        sig_hist,
        acct.get('futures_buying_power', 134)
    )
    print(f"  Drawdown status: {drawdown['status']}")

    # ── Test 2: Hard veto scenario ─────────────────────────────
    print(f"\n{'─' * 65}")
    print("SCENARIO: Hard veto — spread too wide (0.25%)")
    print(f"{'─' * 65}")

    friction_wide = compute_friction_analysis(0.25, 2316.0)
    print(f"Friction status: {friction_wide['status']}")
    print(f"(GPT reads this label and decides whether to veto)")

    # ── Test 3: Live assessment ────────────────────────────────
    print(f"\n{'─' * 65}")
    print("SCENARIO: Live data — real account + real market")
    print(f"{'─' * 65}")

    rows = get_24h_rows()
    if not rows:
        print("No hourly data yet — run observer longer")
        return

    print(f"Using {len(rows)} hourly rows")
    print(f"Latest: {rows[0]['timestamp']} ETH=${rows[0]['eth_close']}")
    print(f"\nCalling Balthasar with live data...")

    vote = balthasar.assess(rows=rows)
    print()
    print_vote(vote)

    # Validation
    print(f"\n{'─' * 65}")
    assert vote['vote'] in ['long','short','flat'], "Invalid vote"
    assert vote['conviction'] in ['high','medium','low'], "Invalid conviction"
    assert isinstance(vote['veto'], bool), "Veto must be bool"
    if vote['veto']:
        assert vote['veto_reason'], "Veto requires veto_reason"
    else:
        assert vote.get('veto_reason') is None, "No veto = no veto_reason"
    print("✓ Structure validation passed")
    print(f"\nBalthasar is ready.")


if __name__ == "__main__":
    run()
