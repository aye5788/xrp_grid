# =============================================================
# MELCHIOR LIVE DATA TEST
# Pulls the last 24 hourly rows from the observer database
# and feeds them directly to Melchior with full context.
# Run from ~/eth_observer with venv active:
#   python -m magi.test_melchior_live
# =============================================================

import os
import sys
sys.path.insert(0, os.path.expanduser("~/eth_observer"))

from magi.melchior import Melchior, get_24h_rows, compute_trends


def run():
    print("=" * 65)
    print("MELCHIOR LIVE DATA TEST — 24H CONTEXT")
    print("=" * 65)

    rows = get_24h_rows()

    if not rows:
        print("No hourly data in database yet. Let the observer run longer.")
        return

    print(f"\nDatabase has {len(rows)} hourly rows available.")
    print(f"Range: {rows[-1]['timestamp']} -> {rows[0]['timestamp']}")

    # Show the data table
    print(f"\n{'Timestamp':<20} {'ETH':>8} {'ETH%':>7} "
          f"{'BTC%':>7} {'VWAP dev%':>10} {'Vol':>8} {'Funding':>12}")
    print("─" * 76)
    for r in rows:
        def fmt(v, d=3):
            try: return round(float(v), d)
            except: return "NULL"
        print(
            f"{str(r.get('timestamp','')):<20} "
            f"${fmt(r.get('eth_close'),2):>7} "
            f"{fmt(r.get('eth_ret_pct')):>7}% "
            f"{fmt(r.get('btc_ret_pct')):>7}% "
            f"{fmt(r.get('vwap_dev_pct')):>9}% "
            f"{str(r.get('vol_regime') or 'N/A'):>8} "
            f"{fmt(r.get('funding_rate'),8):>12}"
        )

    # Show trends
    trends = compute_trends(rows)
    print(f"\n24H TREND SUMMARY:")
    print(f"  VWAP deviation trend:  {trends.get('vwap_dev_trend')}")
    print(f"  BTC direction (6h):    {trends.get('btc_6h_direction')} "
          f"({trends.get('btc_6h_total_pct')}% cumulative)")
    print(f"  Vol regime:            {trends.get('vol_regime_summary')}")
    print(f"  Funding direction:     {trends.get('funding_direction')}")
    print(f"  24h range:             "
          f"${trends.get('price_24h_low'):.2f} — ${trends.get('price_24h_high'):.2f}")
    print(f"  Position in range:     {trends.get('price_range_position_pct')}%")
    print(f"  Recent signals (6h):   "
          f"{trends.get('long_signals_last_6h')} long, "
          f"{trends.get('short_signals_last_6h')} short")

    # Null field check
    latest = rows[0]
    null_fields = [k for k, v in latest.items() if v is None]
    if null_fields:
        print(f"\n  ⚠ Null fields in latest row: {null_fields}")
        print(f"  These will populate as more data sources are added.")

    # Run Melchior
    print(f"\n{'─' * 65}")
    print(f"Sending to Melchior...")
    print(f"{'─' * 65}")

    melchior = Melchior()
    vote = melchior.assess(rows=rows)

    print(f"\nMELCHIOR VOTE:")
    print(f"  Vote:       {vote['vote'].upper()}")
    print(f"  Conviction: {vote['conviction']}")
    print(f"  Status:     {vote['status']}")
    print(f"\n  Reasoning:")
    print(f"  {vote['reasoning']}")
    print(f"\n  Concerns:")
    for c in vote['concerns']:
        print(f"  - {c}")


if __name__ == "__main__":
    run()
