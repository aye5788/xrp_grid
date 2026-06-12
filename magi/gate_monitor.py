"""
magi/gate_monitor.py — continuous, always-on monitoring service that
wires Kraken WebSocket v2 events to magi/gate.py predicate evaluation.

Architecture:

  KrakenWebSocketClient
       │ ticker / ohlc / status / state-change callbacks
       ▼
  GateMonitor
       │ - persists ws_health rows
       │ - aggregates 1m bars → 1h candles
       │ - on 1h boundary: recomputes indicators via observer pipeline
       │ - on 1h boundary: calls gate.evaluate_gate(observer.db)
       ▼
  magi_gate_events table (gate.evaluate_gate writes the rows)

Degradation: if WS fails to recover within REST_FALLBACK_GRACE_SEC,
GateMonitor launches a REST polling thread that calls observer-style
REST OHLC fetches at REST_FALLBACK_INTERVAL_SEC cadence and feeds them
through the same closed-bar handler. WS reconnect continues in the
background; when WS reconnects, REST fallback shuts down.

The MAGI council never calls into this module. The council reads
accumulated magi_gate_events at its own 4h cadence via
orchestrator.build_world_state.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / '.env')

from grid.exchanges.kraken_ws import (  # noqa: E402
    KrakenWebSocketClient,
    DEFAULT_OHLC_INTERVAL_MIN,
    HEARTBEAT_DEAD_THRESHOLD_SEC,
)

log = logging.getLogger(__name__)


DB_PATH = '/root/xrp_grid/observer.db'
SYMBOL = "XRP/USD"

# REST fallback engages if WS hasn't been 'connected' for this long
REST_FALLBACK_GRACE_SEC = 60.0
REST_FALLBACK_INTERVAL_SEC = 30.0

# Periodic ws_health row cadence (in addition to state-change rows)
WS_HEALTH_HEARTBEAT_SEC = 30.0

# Reconnect-churn alert threshold: a 'reconnecting' state change only writes
# an alert row when the client has reconnected this many times in the last
# hour (flapping). Single blips recover in seconds and are visible on the
# GATE MON chip / ws_health; a sustained outage is alerted critically by the
# REST-fallback engagement instead.
WS_FLAP_ALERT_COUNT_1H = 6

# Bounded buffers for 1m bars accumulated in-memory pending 1h aggregation
ONE_HOUR_BUFFER_CAP = 90  # 1h = 60 minutes; cap with margin for catch-up
BTC_FETCH_INTERVAL_SEC = 3600.0  # observer.compute_indicators wants BTC 1d


class GateMonitor:
    """The always-on gate monitoring service. start() returns immediately;
    background thread does the work. shutdown() stops cleanly."""

    def __init__(self, db_path: str = DB_PATH, symbol: str = SYMBOL):
        self.db_path = db_path
        self.symbol = symbol

        self._ws_client: Optional[KrakenWebSocketClient] = None
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None

        # State for ws_health persistence
        self._last_health_write = 0.0
        self._last_state = "starting"

        # 1m bars accumulating toward the next 1h aggregation. Keyed by
        # 1h bucket key (string 'YYYY-MM-DDTHH:00:00Z') -> list of 1m
        # bar dicts. Older buckets evict at ONE_HOUR_BUFFER_CAP.
        self._bars_1m_by_hour: dict = {}
        self._bars_1m_lock = threading.Lock()

        # Last 1h bucket we evaluated the gate against — prevents double-eval
        self._last_evaluated_hour: Optional[str] = None

        # REST fallback state
        self._rest_fallback_thread: Optional[threading.Thread] = None
        self._rest_fallback_active = False
        self._ws_down_since: Optional[float] = None
        # Reconnect-churn alert dedup (max one flap warn per hour)
        self._last_flap_alert_at: float = 0.0

    # ----- lifecycle -----

    def start(self):
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            log.warning("GateMonitor.start() called twice; ignoring")
            return
        self._stop_event.clear()
        self._write_ws_health("starting", "GateMonitor.start()")
        self._ws_client = KrakenWebSocketClient(
            symbols=[self.symbol],
            ohlc_interval_min=DEFAULT_OHLC_INTERVAL_MIN,
        )
        self._ws_client.on_ticker = self._on_ticker
        self._ws_client.on_ohlc_closed = self._on_ohlc_closed
        self._ws_client.on_status = self._on_status
        self._ws_client.on_state_change = self._on_state_change
        self._ws_client.start()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="gate_monitor",
            daemon=True,
        )
        self._monitor_thread.start()
        log.info("GateMonitor started")

    def shutdown(self, timeout: float = 5.0):
        log.info("GateMonitor.shutdown() called")
        self._stop_event.set()
        if self._ws_client is not None:
            self._ws_client.shutdown(timeout=timeout)
        if self._rest_fallback_thread is not None:
            self._rest_fallback_thread.join(timeout=2.0)
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=timeout)
        self._write_ws_health("disconnected", "GateMonitor.shutdown")

    # ----- monitor loop -----

    def _monitor_loop(self):
        """Periodic housekeeping: write heartbeat ws_health rows,
        manage REST fallback engagement based on WS state."""
        while not self._stop_event.is_set():
            try:
                self._tick_health()
                self._tick_rest_fallback()
            except Exception as e:
                log.exception("monitor loop iteration raised: %s", e)
            self._stop_event.wait(2.0)

    def _tick_health(self):
        """Write a periodic ws_health row even when state hasn't changed.
        Lets the dashboard show live last-heartbeat-age."""
        if time.time() - self._last_health_write < WS_HEALTH_HEARTBEAT_SEC:
            return
        if self._ws_client is None:
            return
        state = self._ws_client.state
        if self._rest_fallback_active:
            state = "degraded"
        self._write_ws_health(state, "periodic")

    def _tick_rest_fallback(self):
        """Engage REST fallback if WS has been not-connected for too long."""
        if self._ws_client is None:
            return
        if self._ws_client.state == "connected" and not self._rest_fallback_active:
            self._ws_down_since = None
            return
        # WS is not-connected. Track how long
        if self._ws_client.state != "connected":
            if self._ws_down_since is None:
                self._ws_down_since = time.time()
                log.warning("ws not connected (state=%s) — fallback timer started",
                            self._ws_client.state)
            elif (not self._rest_fallback_active
                  and time.time() - self._ws_down_since >= REST_FALLBACK_GRACE_SEC):
                self._engage_rest_fallback()
        else:
            # WS reconnected and we were on fallback — shut REST fallback down
            if self._rest_fallback_active:
                log.info("ws reconnected — disengaging REST fallback")
                self._disengage_rest_fallback()
                self._ws_down_since = None
                self._write_ws_health("connected", "ws recovered, REST fallback disengaged")

    # ----- ws callbacks -----

    def _on_state_change(self, new_state: str, notes: str):
        # Promote to 'degraded' label if REST fallback active
        label = "degraded" if (self._rest_fallback_active and new_state != "connected") else new_state
        self._write_ws_health(label, f"state_change: {notes}")

        # Alert policy (tightened 2026-06-10 — transient reconnects were
        # writing one open warn row each and cluttering the dashboard):
        #   - single 'reconnecting' blips: NO alert row. They recover in
        #     seconds; the GATE MON chip + ws_health already show them live,
        #     and a real outage is caught by _engage_rest_fallback's critical
        #     alert after REST_FALLBACK_GRACE_SEC.
        #   - flapping ('reconnecting' with reconnect_count_1h >= threshold):
        #     one warn per hour, not one per reconnect.
        #   - 'disconnected' (client gave up): critical, unchanged.
        #   - recovery to 'connected': auto-resolve any open gate_ws_down
        #     rows — the alert means "WS down NOW", so recovery clears it.
        if new_state == "connected":
            self._auto_resolve_ws_alerts()
            return
        if new_state == "disconnected":
            self._insert_ws_alert('critical', new_state, notes)
        elif new_state == "reconnecting":
            rc = self._ws_client.reconnect_count_1h if self._ws_client else 0
            now = time.time()
            if (rc or 0) >= WS_FLAP_ALERT_COUNT_1H and \
                    now - self._last_flap_alert_at >= 3600:
                self._last_flap_alert_at = now
                self._insert_ws_alert('warn', new_state, notes)

    def _insert_ws_alert(self, severity: str, new_state: str, notes: str):
        try:
            from database import insert_alert
            age = self._ws_client.last_message_age_sec if self._ws_client else None
            rc = self._ws_client.reconnect_count_1h if self._ws_client else None
            insert_alert(
                severity=severity,
                category='gate_ws_down',
                message=(
                    f"gate_monitor WS state={new_state}. "
                    f"reconnect_count_1h={rc} last_message_age_sec={age}. "
                    f"notes={notes}"
                ),
            )
        except Exception as e:
            log.warning("insert_alert(gate_ws_down) failed: %r", e)

    def _auto_resolve_ws_alerts(self):
        """WS is back — resolve any open gate_ws_down rows so the dashboard
        ALERTS banner only shows CURRENT problems. History stays in the
        table (resolved=1 + resolved_at)."""
        try:
            import sqlite3 as _sq
            conn = _sq.connect(self.db_path)
            try:
                cur = conn.execute(
                    "UPDATE magi_alerts SET resolved=1, "
                    "resolved_at=datetime('now') "
                    "WHERE category='gate_ws_down' AND resolved=0",
                )
                conn.commit()
                if cur.rowcount:
                    log.info("ws recovered — auto-resolved %d open "
                             "gate_ws_down alert(s)", cur.rowcount)
            finally:
                conn.close()
        except Exception as e:
            log.warning("auto-resolve gate_ws_down failed: %r", e)

    def _on_status(self, status_payload: dict):
        # Just log; subscription gating happens inside KrakenWebSocketClient
        log.info("kraken WS status: %s", status_payload)

    def _on_ticker(self, tick: dict):
        # No tick-cadenced triggers in the current set. Placeholder for
        # future T1-as-sliding-window or similar. Ticks DO reset the
        # heartbeat timer in the client.
        pass

    def _on_ohlc_closed(self, closed_bar: dict):
        """A 1m bar just closed. Write it to candles, aggregate into
        the hour bucket, and if the bar's hour boundary advances,
        materialise the 1h candle + recompute indicators + evaluate gate."""
        try:
            self._write_1m_bar_to_candles(closed_bar)
        except Exception as e:
            log.exception("write_1m_bar_to_candles raised: %s", e)
        try:
            hour_key, hour_complete = self._buffer_1m_bar(closed_bar)
        except Exception as e:
            log.exception("buffer_1m_bar raised: %s", e)
            return
        if hour_complete and hour_key != self._last_evaluated_hour:
            try:
                self._materialise_1h_candle(hour_key)
                self._recompute_indicators_and_eval()
                self._last_evaluated_hour = hour_key
            except Exception as e:
                log.exception("1h close handling raised: %s", e)

    # ----- 1m → 1h aggregation -----

    def _bar_hour_key(self, closed_bar: dict) -> str:
        """Return 'YYYY-MM-DDTHH:00:00Z' for the hour bucket this 1m
        bar belongs to. interval_begin is RFC3339 with nanoseconds."""
        iv = closed_bar.get("interval_begin") or ""
        # Truncate to YYYY-MM-DDTHH:00:00Z
        if len(iv) >= 13:
            return iv[:13] + ":00:00Z"
        return iv

    def _buffer_1m_bar(self, closed_bar: dict) -> tuple:
        """Append closed_bar to the appropriate hour bucket. Return
        (hour_key, hour_complete) where hour_complete is True iff this
        bar was the :59 minute of the bucket (i.e., the hour is now
        fully observed)."""
        hour_key = self._bar_hour_key(closed_bar)
        with self._bars_1m_lock:
            self._bars_1m_by_hour.setdefault(hour_key, []).append(closed_bar)
            # Evict oldest buckets if we have too many
            if len(self._bars_1m_by_hour) > ONE_HOUR_BUFFER_CAP:
                oldest = min(self._bars_1m_by_hour.keys())
                self._bars_1m_by_hour.pop(oldest, None)
        # Is the minute :59? Then the hour is complete.
        iv = closed_bar.get("interval_begin") or ""
        # interval_begin format: 2024-01-01T15:59:00.000000Z
        try:
            minute = int(iv[14:16])
        except (ValueError, IndexError):
            return hour_key, False
        return hour_key, (minute == 59)

    def _materialise_1h_candle(self, hour_key: str):
        """Aggregate the 1m bars in the given hour bucket into a 1h
        candle and upsert it into candles(timeframe='1h')."""
        with self._bars_1m_lock:
            bars = list(self._bars_1m_by_hour.get(hour_key, []))
        if not bars:
            log.warning("materialise_1h_candle: no bars for %s", hour_key)
            return
        # Sort by interval_begin ascending
        bars.sort(key=lambda b: b.get("interval_begin", ""))
        try:
            open_p = float(bars[0]["open"])
            high_p = max(float(b["high"]) for b in bars)
            low_p = min(float(b["low"]) for b in bars)
            close_p = float(bars[-1]["close"])
            volume = sum(float(b.get("volume") or 0) for b in bars)
        except (KeyError, TypeError, ValueError) as e:
            log.warning("1h aggregate parse error: %s", e)
            return
        # candles.timestamp uses ISO; the live observer stores '...+00:00'
        # — match that format. interval_begin is 'YYYY-MM-DDTHH:00:00.000000Z'.
        timestamp = hour_key[:-1] + "+00:00"  # 'Z' -> '+00:00'
        try:
            from database import insert_candle
            insert_candle(timestamp, '1h', open_p, high_p, low_p, close_p, volume)
            log.info("1h candle materialised from WS: %s o=%.5f h=%.5f l=%.5f c=%.5f vol=%.2f n_1m=%d",
                     timestamp, open_p, high_p, low_p, close_p, volume, len(bars))
        except Exception as e:
            log.exception("insert_candle for WS 1h candle raised: %s", e)

    def _write_1m_bar_to_candles(self, closed_bar: dict):
        """Optionally persist 1m bars too. The existing candles schema
        supports any string timeframe; we use '1m'. Bounded growth is
        fine — these are small rows."""
        iv = closed_bar.get("interval_begin") or ""
        if not iv:
            return
        try:
            timestamp = iv[:19] + "+00:00" if len(iv) >= 19 else iv
            from database import insert_candle
            insert_candle(
                timestamp, '1m',
                float(closed_bar.get("open") or 0),
                float(closed_bar.get("high") or 0),
                float(closed_bar.get("low") or 0),
                float(closed_bar.get("close") or 0),
                float(closed_bar.get("volume") or 0),
            )
        except Exception as e:
            log.warning("insert_candle 1m raised: %s", e)

    # ----- indicator recompute + gate eval -----

    def _recompute_indicators_and_eval(self):
        """Run the existing observer indicator pipeline against current
        candle history, then call gate.evaluate_gate. Same compute path
        observer.poll_cycle uses — we just trigger it on the hour close
        instead of on the 10-min REST poll."""
        try:
            from database import get_candles, upsert_indicators
            from observer import compute_indicators, get_candles_coinbase
            # XRP candles from our DB (which the WS just updated)
            xrp_1h = list(reversed(get_candles('1h', limit=300)))
            xrp_6h = list(reversed(get_candles('6h', limit=100))) if False else []
            # No 6h candles stored — observer's pipeline accepts empty list
            xrp_1d = list(reversed(get_candles('1d', limit=300)))
            # BTC daily from Coinbase (always — same as observer)
            btc_1d = get_candles_coinbase("BTC-USD", "ONE_DAY", 300)
            indicators = compute_indicators(xrp_1h, xrp_6h, xrp_1d, btc_1d)
            if indicators:
                ts = indicators.pop('timestamp')
                tf = indicators.pop('timeframe')
                upsert_indicators(ts, tf, indicators)
                log.info("gate_monitor: indicators recomputed at 1h close — "
                         "vol_regime=%s vwap_dev=%s",
                         indicators.get('vol_regime'),
                         indicators.get('vwap_dev_pct'))
            else:
                log.warning("gate_monitor: compute_indicators returned None")
        except Exception as e:
            log.exception("gate_monitor indicator recompute raised: %s", e)
        # Now evaluate the gate against fresh data
        try:
            from magi.gate import evaluate_gate
            fired = evaluate_gate(self.db_path)
            if fired:
                log.info("gate_monitor: triggers fired %s", fired)
        except Exception as e:
            log.exception("gate_monitor evaluate_gate raised: %s", e)

    # ----- REST fallback -----

    def _engage_rest_fallback(self):
        """Spawn a REST polling thread that fetches Kraken's public
        OHLC endpoint at REST_FALLBACK_INTERVAL_SEC cadence and feeds
        synthetic 'closed bar' events through the same handler."""
        if self._rest_fallback_active:
            return
        log.warning("engaging REST fallback (WS down for %.1fs)",
                    time.time() - (self._ws_down_since or time.time()))
        self._rest_fallback_active = True
        try:
            from database import insert_alert
            insert_alert(
                severity='critical',
                category='gate_ws_down',
                message=(
                    f"gate_monitor engaging REST fallback. WS down for "
                    f"{REST_FALLBACK_GRACE_SEC}s+. Polling Kraken REST at "
                    f"{REST_FALLBACK_INTERVAL_SEC}s cadence."
                ),
            )
        except Exception as e:
            log.warning("insert_alert(rest_fallback) failed: %r", e)
        self._rest_fallback_thread = threading.Thread(
            target=self._rest_fallback_loop,
            name="gate_monitor_rest_fallback",
            daemon=True,
        )
        self._rest_fallback_thread.start()

    def _disengage_rest_fallback(self):
        self._rest_fallback_active = False
        # The fallback thread sees the flag and exits

    def _rest_fallback_loop(self):
        """Poll Kraken REST every REST_FALLBACK_INTERVAL_SEC and fetch
        the most recent completed 1h candle. If it differs from what
        we have (or we don't have it), upsert and evaluate gate."""
        last_seen_iv_begin: Optional[str] = None
        while self._rest_fallback_active and not self._stop_event.is_set():
            try:
                from observer import get_candles_xrp
                xrp_1h = get_candles_xrp("ONE_HOUR", 3)
                # Take the most recent COMPLETED candle (skip the
                # in-progress one)
                now_h = datetime.utcnow().replace(
                    minute=0, second=0, microsecond=0
                ).isoformat() + "+00:00"
                completed = [c for c in xrp_1h
                             if c.get('timestamp', '') < now_h]
                if completed:
                    latest = max(completed, key=lambda c: c.get('timestamp', ''))
                    iv_begin = latest.get('timestamp')
                    if iv_begin != last_seen_iv_begin:
                        last_seen_iv_begin = iv_begin
                        from database import insert_candle
                        insert_candle(
                            iv_begin, '1h',
                            float(latest['open']), float(latest['high']),
                            float(latest['low']), float(latest['close']),
                            float(latest.get('volume') or 0),
                        )
                        log.info("REST fallback: 1h candle from REST: %s close=%.5f",
                                 iv_begin, float(latest['close']))
                        self._recompute_indicators_and_eval()
            except Exception as e:
                log.warning("REST fallback iteration raised: %r", e)
            # Sleep with stop_event interrupt
            end_t = time.time() + REST_FALLBACK_INTERVAL_SEC
            while time.time() < end_t and self._rest_fallback_active and not self._stop_event.is_set():
                time.sleep(1.0)
        log.info("REST fallback loop exiting")

    # ----- ws_health persistence -----

    def _write_ws_health(self, state: str, notes: str = ""):
        if self._ws_client is None:
            last_age = None
            rc = 0
        else:
            last_age = self._ws_client.last_message_age_sec
            rc = self._ws_client.reconnect_count_1h
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute(
                "INSERT INTO ws_health (timestamp, state, "
                "last_heartbeat_age_sec, reconnect_count_1h, "
                "last_tick_age_sec, notes) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), state, last_age, rc, last_age, notes[:500]),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning("ws_health insert failed: %r", e)
        self._last_health_write = time.time()
        self._last_state = state


# Module-level singleton for the scheduler to launch
_singleton: Optional[GateMonitor] = None


def get_monitor() -> GateMonitor:
    global _singleton
    if _singleton is None:
        _singleton = GateMonitor()
    return _singleton


def start_in_background() -> GateMonitor:
    """Convenience helper for scheduler.main(). Returns the monitor
    instance so the scheduler can call shutdown() at process exit."""
    m = get_monitor()
    m.start()
    return m
