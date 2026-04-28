# =============================================================
# CASPER TEST HARNESS
# Tests all data sources then runs a live Gemini assessment.
# Run from ~/eth_observer with venv active:
#   python -m magi.test_casper
# =============================================================

import os
import sys
sys.path.insert(0, os.path.expanduser("~/eth_observer"))

from magi.casper import (
    Casper,
    get_coingecko_data,
    get_fear_greed,
    get_dxy_data,
    get_yield_data,
    get_observer_context,
    build_context,
)


def print_vote(vote: dict):
    print(f"  Vote:       {vote['vote'].upper()}")
    print(f"  Conviction: {vote['conviction']}")
    print(f"  Macro lean: {vote['macro_lean']}")
    print(f"  Veto:       {vote['veto']}")
    print(f"  Status:     {vote['status']}")
    print(f"\n  Reasoning:")
    print(f"  {vote['reasoning']}")
    print(f"\n  Concerns:")
    for c in vote['concerns']:
        print(f"  - {c}")


def run():
    print("=" * 65)
    print("CASPER-3 TEST HARNESS")
    print("=" * 65)

    # ── Data source validation ─────────────────────────────────
    print("\n--- DATA SOURCE VALIDATION ---")

    print("\n1. CoinGecko:")
    cg = get_coingecko_data()
    print(f"   Status: {cg.get('status')}")
    if cg.get('status') == 'ok':
        print(f"   BTC dominance: {cg['btc_dominance_pct']}%")
        print(f"   ETH dominance: {cg['eth_dominance_pct']}%")
        print(f"   Market cap 24h change: {cg['market_cap_change_24h_pct']}%")

    print("\n2. Fear & Greed:")
    fg = get_fear_greed()
    print(f"   Status: {fg.get('status')}")
    if fg.get('status') == 'ok':
        print(f"   Current: {fg['fear_greed_index']} ({fg['fear_greed_classification']})")
        print(f"   3-day trend: {fg['fear_greed_3d_trend']}")
        print(f"   3-day values: {fg['fear_greed_3d_values']}")

    print("\n3. DXY:")
    dxy = get_dxy_data()
    print(f"   Status: {dxy.get('status')}")
    if dxy.get('status') == 'ok':
        print(f"   Current: {dxy['dxy_close']}")
        print(f"   Change: {dxy['dxy_change_pct']}%")
        print(f"   Direction: {dxy['dxy_direction']}")

    print("\n4. 10-Year Yield (FRED):")
    yields = get_yield_data()
    print(f"   Status: {yields.get('status')}")
    if yields.get('status') == 'ok':
        print(f"   Current: {yields['yield_10y']}%")
        print(f"   Change: {yields['yield_change_bps']} bps")
        print(f"   Direction: {yields['yield_direction']}")
    else:
        print(f"   Error: {yields.get('error')}")

    print("\n5. Observer context:")
    obs = get_observer_context()
    print(f"   Status: {obs.get('status')}")
    if obs.get('status') == 'ok':
        print(f"   Premium: {obs.get('premium_pct')}%")
        print(f"   Premium direction: {obs.get('premium_direction')}")
        print(f"   Vol regime: {obs.get('vol_regime')}")
        print(f"   BTC 6h direction: {obs.get('btc_6h_direction')}")

    # ── Live Casper assessment ─────────────────────────────────
    print(f"\n{'─' * 65}")
    print("LIVE ASSESSMENT — calling Gemini...")
    print(f"{'─' * 65}")

    casper = Casper()
    vote   = casper.assess()

    print()
    print_vote(vote)

    # ── Structure validation ───────────────────────────────────
    print(f"\n{'─' * 65}")
    assert vote['vote'] in ['long','short','flat'], "Invalid vote"
    assert vote['conviction'] in ['high','medium','low'], "Invalid conviction"
    assert vote['macro_lean'] in ['risk-on','risk-off','mixed'], "Invalid macro_lean"
    assert vote['veto'] == False, "Casper must never veto"
    assert isinstance(vote['concerns'], list), "Concerns must be list"
    assert len(vote['reasoning']) > 20, "Reasoning too short"
    print("✓ Structure validation passed")
    print("\nCasper is ready.")


if __name__ == "__main__":
    run()
