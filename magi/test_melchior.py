# =============================================================
# MELCHIOR TEST HARNESS
# Tests Melchior with synthetic signals representing three
# realistic scenarios. Validates JSON output and reasoning.
# Run from ~/eth_observer with venv active:
#   python -m magi.test_melchior
# =============================================================

import json
import sys
import os
sys.path.insert(0, os.path.expanduser("~/eth_observer"))

from magi.melchior import Melchior

# ── Test scenarios ────────────────────────────────────────────
# Each represents a realistic market state

SCENARIOS = {

    "strong_short_signal": {
        # ETH well above VWAP, high vol, BTC quiet
        # Expect: short, high conviction
        "description": "ETH 1.2% above VWAP, high vol, BTC flat — clean short setup",
        "signals": {
            "eth_close":        2380.0,
            "eth_ret_pct":      1.24,
            "btc_ret_pct":      0.12,
            "eth_btc_ratio_ret": 1.12,
            "vwap_24h":         2351.8,
            "vwap_dev_pct":     1.19,
            "vol_24h_std":      1.21,
            "vol_regime":       "high",
            "avg_spread_pct":   0.031,
            "funding_rate":     0.000012,
            "hour_of_day":      21,
            "day_of_week":      1,
            "trigger_reason":   "vwap_deviation_threshold"
        }
    },

    "weak_long_signal": {
        # ETH modestly below VWAP, medium vol, BTC moving
        # Expect: flat or long with low conviction
        "description": "ETH 0.6% below VWAP, medium vol, BTC moving 0.7% — weak setup",
        "signals": {
            "eth_close":        2308.0,
            "eth_ret_pct":      -0.58,
            "btc_ret_pct":      -0.71,
            "eth_btc_ratio_ret": 0.13,
            "vwap_24h":         2322.0,
            "vwap_dev_pct":     -0.60,
            "vol_24h_std":      0.71,
            "vol_regime":       "medium",
            "avg_spread_pct":   0.038,
            "funding_rate":     0.000006,
            "hour_of_day":      14,
            "day_of_week":      3,
            "trigger_reason":   "vwap_deviation_threshold"
        }
    },

    "missing_data": {
        # Several null fields — data quality failure scenario
        # Expect: flat, low conviction, concerns about missing data
        "description": "Missing vol_regime and vwap_dev — data quality failure",
        "signals": {
            "eth_close":        2321.0,
            "eth_ret_pct":      -0.22,
            "btc_ret_pct":      None,
            "eth_btc_ratio_ret": None,
            "vwap_24h":         None,
            "vwap_dev_pct":     None,
            "vol_24h_std":      None,
            "vol_regime":       None,
            "avg_spread_pct":   0.034,
            "funding_rate":     0.000006,
            "hour_of_day":      16,
            "day_of_week":      0,
            "trigger_reason":   "scheduled"
        }
    }
}


def run_tests():
    print("=" * 60)
    print("MELCHIOR-1 TEST HARNESS")
    print("=" * 60)

    melchior = Melchior()
    results = []

    for name, scenario in SCENARIOS.items():
        print(f"\n{'─' * 60}")
        print(f"SCENARIO: {name}")
        print(f"Setup:    {scenario['description']}")
        print(f"{'─' * 60}")

        try:
            vote = melchior.assess(scenario["signals"])

            print(f"Vote:       {vote['vote'].upper()}")
            print(f"Conviction: {vote['conviction']}")
            print(f"Reasoning:  {vote['reasoning']}")
            print(f"Concerns:   {vote['concerns']}")
            print(f"Veto:       {vote['veto']}")
            print(f"Status:     {vote['status']}")

            # Validate structure
            assert vote["veto"] == False, "Melchior should never veto"
            assert vote["vote"] in ["long","short","flat"], "Invalid vote"
            assert vote["conviction"] in ["high","medium","low"], "Invalid conviction"
            assert isinstance(vote["concerns"], list), "Concerns must be a list"
            assert isinstance(vote["reasoning"], str), "Reasoning must be a string"
            assert len(vote["reasoning"]) > 20, "Reasoning too short"

            print("✓ Structure validation passed")
            results.append((name, "PASS", vote["vote"], vote["conviction"]))

        except AssertionError as e:
            print(f"✗ Validation failed: {e}")
            results.append((name, "FAIL", "—", "—"))

        except Exception as e:
            print(f"✗ Error: {e}")
            results.append((name, "ERROR", "—", "—"))

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"{'Scenario':<25} {'Result':<8} {'Vote':<8} {'Conviction'}")
    print(f"{'─' * 60}")
    for name, result, vote, conviction in results:
        print(f"{name:<25} {result:<8} {vote:<8} {conviction}")

    passed = sum(1 for _, r, _, _ in results if r == "PASS")
    print(f"\n{passed}/{len(results)} scenarios passed")

    if passed == len(results):
        print("\nMelchior is ready. Connect to real data next.")
    else:
        print("\nReview failed scenarios before proceeding.")


if __name__ == "__main__":
    run_tests()
