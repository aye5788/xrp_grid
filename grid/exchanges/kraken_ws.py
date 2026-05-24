"""
grid/exchanges/kraken_ws.py — Kraken WebSocket v2 public-channel client.

Used by magi/gate_monitor.py to drive the gate against streaming
ticker + OHLC data instead of the observer's 10-min REST poll. Public
channels only (ticker, ohlc, status, heartbeat) — no authentication
required.

Design choices and why:

- Library: websocket-client 1.9.0 (sync threaded), NOT the asyncio
  `websockets` library. The existing scheduler is fully synchronous
  with a `while running:` main loop; grafting asyncio onto that would
  require an event loop in a side thread anyway. websocket-client
  natively runs in a thread via WebSocketApp.run_forever() and writes
  to the (thread-safe) sqlite3 module. Net: lower cognitive load.

- Reconnect floor: 5 seconds minimum between attempts. Kraken's docs
  explicitly say "no more quickly than once every 5 seconds" after
  maintenance windows, and Cloudflare bans IPs that exceed ~150
  connection attempts per rolling 10 minutes. Backoff sequence:
  5, 5, 10, 20, 30, 30, 30, ... never less than 5s.

- Heartbeat policy: Kraken sends a `{"channel": "heartbeat"}` only
  when no other channel update has fired in the last second. So an
  active connection's "heartbeat" is ANY inbound message (heartbeat,
  ticker update, ohlc update, status). If no inbound traffic for
  HEARTBEAT_DEAD_THRESHOLD_SEC (default 10), force-close and
  reconnect.

- Active liveness: optional client-sent ping every 5s if no inbound
  message in the last 3s. Reduces false-positive dead-connection
  detection during quiet markets.

- No sequence-number gap detection. Public ticker/ohlc do not carry
  per-channel sequence numbers (only private/execution channels do).
  Use inbound message timestamps to track gaps.

- Subscribe-on-connect, ALWAYS, including on every reconnect. Don't
  declare the connection healthy until the snapshot for each channel
  has been received.

- The status message's `system` field gates subscription: if
  `system != "online"` (e.g., 'maintenance', 'cancel_only',
  'post_only'), wait for the next status message rather than
  subscribing. Cancel_only/post_only still mean the data feed is
  alive; we still subscribe — only 'maintenance' pauses.

Public surface (consumed by gate_monitor):

  client = KrakenWebSocketClient(symbols=["XRP/USD"], log=logger)
  client.on_ticker = lambda payload: ...
  client.on_ohlc_update = lambda payload: ...   # in-progress bar
  client.on_ohlc_closed = lambda closed_bar: ...# bar transition
  client.on_status = lambda payload: ...
  client.on_state_change = lambda new_state, notes: ...
  client.start()           # spawns thread, runs forever
  client.shutdown()        # graceful disconnect

  client.state             # 'starting'|'connected'|'reconnecting'|'disconnected'
  client.last_message_age_sec
  client.reconnect_count_1h
  client.last_tick         # most recent ticker payload dict
  client.last_closed_bar(interval)  # most recent closed bar dict
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Callable, Optional

import websocket  # websocket-client 1.9.0

log = logging.getLogger(__name__)


KRAKEN_WS_V2_URL = "wss://ws.kraken.com/v2"

# Reconnect backoff sequence (seconds). Kraken docs: "no more quickly
# than once every 5 seconds". Cap at 30s to keep recovery time bounded
# but stay well under the 150-attempts-per-10-min IP ban threshold.
RECONNECT_BACKOFF_SEC = [5, 5, 10, 20, 30]

# If no inbound traffic for this many seconds, force-close + reconnect.
# Kraken docs do not prescribe a threshold; 10s is conservative given
# heartbeats are 1Hz and any channel update also resets the timer.
HEARTBEAT_DEAD_THRESHOLD_SEC = 10.0

# Client-sent ping interval (proactive liveness probe). Only sent if
# no inbound traffic in PING_QUIET_SEC.
PING_INTERVAL_SEC = 5.0
PING_QUIET_SEC = 3.0

# Bounded buffer of reconnect timestamps for the "reconnects in last 1h"
# metric exposed to dashboard.
RECONNECT_HISTORY_CAP = 200

# Default channels to subscribe on connect. OHLC interval is in MINUTES
# per Kraken docs (1 = 1 minute, 60 = 1 hour). We subscribe to interval=1
# and aggregate to 1h client-side in gate_monitor.
DEFAULT_OHLC_INTERVAL_MIN = 1


class KrakenWebSocketClient:
    """Public-channel Kraken WS v2 client with reconnect + heartbeat
    monitoring + status gating. Thread-based: start() spawns a worker
    thread that runs the connection loop forever until shutdown()."""

    def __init__(
        self,
        symbols: list,
        ohlc_interval_min: int = DEFAULT_OHLC_INTERVAL_MIN,
        url: str = KRAKEN_WS_V2_URL,
    ):
        self.symbols = list(symbols)
        self.ohlc_interval_min = int(ohlc_interval_min)
        self.url = url

        # Callback slots — set by gate_monitor before start()
        self.on_ticker: Optional[Callable[[dict], None]] = None
        self.on_ohlc_update: Optional[Callable[[dict], None]] = None
        self.on_ohlc_closed: Optional[Callable[[dict], None]] = None
        self.on_status: Optional[Callable[[dict], None]] = None
        self.on_state_change: Optional[Callable[[str, str], None]] = None

        # State exposed to callers
        self.state: str = "starting"
        self.last_message_time: float = 0.0
        self.last_tick: Optional[dict] = None
        self._closed_bars: dict = {}  # interval (min) -> latest closed bar dict

        # Internal
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._reconnect_attempt = 0
        self._reconnect_history: deque = deque(maxlen=RECONNECT_HISTORY_CAP)

        # In-progress 1m bar tracking — interval_begin -> last seen payload
        # When the next message has a NEWER interval_begin, the prior bar
        # is closed; we surface it via on_ohlc_closed and replace the
        # tracking entry.
        self._inflight_ohlc: dict = {}  # interval_min -> {interval_begin, payload}

        # Subscription state — gated by status.system == 'online'
        self._subscribed = False
        self._system_status: Optional[str] = None

    # ----- lifecycle -----

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            log.warning("KrakenWebSocketClient.start() called twice; ignoring")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_forever_loop,
            name="kraken_ws_main",
            daemon=True,
        )
        self._thread.start()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_watcher,
            name="kraken_ws_heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
        log.info("KrakenWebSocketClient started (symbols=%s)", self.symbols)

    def shutdown(self, timeout: float = 5.0):
        log.info("KrakenWebSocketClient.shutdown() called")
        self._stop_event.set()
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception as e:
            log.warning("ws.close() during shutdown raised: %r", e)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=1.0)
        self._set_state("disconnected", "shutdown")

    # ----- public properties -----

    @property
    def last_message_age_sec(self) -> Optional[float]:
        if self.last_message_time <= 0:
            return None
        return time.time() - self.last_message_time

    @property
    def reconnect_count_1h(self) -> int:
        cutoff = time.time() - 3600.0
        return sum(1 for t in self._reconnect_history if t >= cutoff)

    def last_closed_bar(self, interval_min: int) -> Optional[dict]:
        return self._closed_bars.get(int(interval_min))

    # ----- main loop -----

    def _run_forever_loop(self):
        """Outer reconnect loop. Inner ws.run_forever() blocks until
        the connection drops; this loop applies backoff and retries."""
        while not self._stop_event.is_set():
            try:
                backoff = self._current_backoff()
                if self._reconnect_attempt > 0:
                    log.info("ws reconnect attempt %d after %ds",
                             self._reconnect_attempt, backoff)
                    self._set_state("reconnecting",
                                    f"attempt={self._reconnect_attempt} backoff={backoff}s")
                    # Sleep with interruption check
                    end_t = time.time() + backoff
                    while time.time() < end_t and not self._stop_event.is_set():
                        time.sleep(0.5)
                    if self._stop_event.is_set():
                        break

                self._subscribed = False
                self._system_status = None
                self._ws = websocket.WebSocketApp(
                    self.url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                # ping_interval=0 disables websocket-protocol pings; we use
                # app-level ping in _heartbeat_watcher instead.
                self._ws.run_forever(
                    ping_interval=0,
                    skip_utf8_validation=True,
                )
            except Exception as e:
                log.exception("ws run_forever raised: %s", e)
            # Connection ended (clean or otherwise) — record + retry
            if not self._stop_event.is_set():
                self._reconnect_history.append(time.time())
                self._reconnect_attempt += 1
        self._set_state("disconnected", "main loop exited")

    def _current_backoff(self) -> int:
        if self._reconnect_attempt <= 0:
            return 0
        idx = min(self._reconnect_attempt - 1, len(RECONNECT_BACKOFF_SEC) - 1)
        return RECONNECT_BACKOFF_SEC[idx]

    def _heartbeat_watcher(self):
        """Force-close + reconnect when inbound traffic stalls. Also
        sends app-level ping during quiet periods."""
        while not self._stop_event.is_set():
            try:
                time.sleep(1.0)
                if self.state != "connected":
                    continue
                age = self.last_message_age_sec
                if age is None:
                    continue
                if age > HEARTBEAT_DEAD_THRESHOLD_SEC:
                    log.warning(
                        "ws heartbeat watcher: no inbound for %.1fs > %.1fs — forcing reconnect",
                        age, HEARTBEAT_DEAD_THRESHOLD_SEC,
                    )
                    try:
                        if self._ws is not None:
                            self._ws.close()
                    except Exception:
                        pass
                    continue
                if age > PING_QUIET_SEC:
                    # Send app-level ping; Kraken will respond with pong,
                    # which counts as inbound traffic and resets the timer.
                    self._send_ping()
            except Exception as e:
                log.warning("heartbeat watcher iteration raised: %r", e)

    def _send_ping(self):
        if self._ws is None:
            return
        try:
            self._ws.send(json.dumps({"method": "ping", "req_id": int(time.time())}))
        except Exception as e:
            log.warning("ping send failed: %r", e)

    # ----- handlers -----

    def _on_open(self, ws):
        log.info("ws connected to %s", self.url)
        self.last_message_time = time.time()
        self._set_state("connected", "ws_open")
        # Reset backoff on successful connect (don't reset until we
        # actually receive a message, to avoid declaring success on a
        # half-open TCP that the heartbeat watcher will then kill)

    def _on_message(self, ws, raw):
        self.last_message_time = time.time()
        try:
            msg = json.loads(raw)
        except Exception as e:
            log.warning("ws on_message could not parse json: %r raw=%s", e, raw[:200])
            return

        # status — channel-system info; gate subscribe on it
        channel = msg.get("channel")
        if channel == "status":
            self._handle_status(msg)
            return
        if channel == "heartbeat":
            # No payload; just resets the no-traffic timer (already done above)
            return
        if channel == "ticker":
            self._handle_ticker(msg)
            return
        if channel == "ohlc":
            self._handle_ohlc(msg)
            return
        # method responses (ping/pong, subscribe ack)
        if msg.get("method") == "pong":
            return
        if msg.get("method") == "subscribe":
            self._handle_subscribe_ack(msg)
            return
        # Anything else — log at debug, don't disrupt
        log.debug("ws unrecognised message: %s", str(msg)[:200])

    def _on_error(self, ws, err):
        log.warning("ws on_error: %r", err)

    def _on_close(self, ws, code, reason):
        log.info("ws closed code=%s reason=%s", code, reason)
        self._subscribed = False
        # state transition happens in run_forever_loop before next attempt

    # ----- channel handlers -----

    def _handle_status(self, msg):
        data = msg.get("data") or []
        if not data:
            return
        entry = data[0]
        self._system_status = entry.get("system")
        if self.on_status:
            try:
                self.on_status(entry)
            except Exception as e:
                log.exception("on_status callback raised: %s", e)
        log.info("ws status: system=%s api_version=%s connection_id=%s",
                 entry.get("system"), entry.get("api_version"),
                 entry.get("connection_id"))
        # Subscribe gating: only subscribe when system says 'online'.
        # 'cancel_only'/'post_only' still send public data; subscribe too.
        # 'maintenance' = wait.
        if self._system_status in ("online", "cancel_only", "post_only") and not self._subscribed:
            self._subscribe_all()
        elif self._system_status == "maintenance":
            log.warning("ws status=maintenance — deferring subscribe until status changes")

    def _subscribe_all(self):
        log.info("ws subscribing: ticker + ohlc(interval=%dmin) for %s",
                 self.ohlc_interval_min, self.symbols)
        try:
            self._ws.send(json.dumps({
                "method": "subscribe",
                "params": {"channel": "ticker", "symbol": self.symbols},
            }))
            self._ws.send(json.dumps({
                "method": "subscribe",
                "params": {
                    "channel": "ohlc",
                    "symbol": self.symbols,
                    "interval": self.ohlc_interval_min,
                },
            }))
            self._subscribed = True
            # We have a working connection AND we've sent subscribes —
            # reset reconnect attempts so future hiccups start at backoff[0]
            self._reconnect_attempt = 0
        except Exception as e:
            log.exception("ws subscribe send raised: %s", e)

    def _handle_subscribe_ack(self, msg):
        ok = msg.get("success") or msg.get("result")
        log.info("ws subscribe ack: channel=%s success=%s",
                 (msg.get("result") or {}).get("channel"), ok)

    def _handle_ticker(self, msg):
        data = msg.get("data") or []
        if not data:
            return
        for entry in data:
            self.last_tick = entry
            if self.on_ticker:
                try:
                    self.on_ticker(entry)
                except Exception as e:
                    log.exception("on_ticker callback raised: %s", e)

    def _handle_ohlc(self, msg):
        data = msg.get("data") or []
        msg_type = msg.get("type")  # 'snapshot' or 'update'
        for entry in data:
            interval = int(entry.get("interval") or 0)
            iv_begin = entry.get("interval_begin")
            if not interval or not iv_begin:
                continue
            prior = self._inflight_ohlc.get(interval)
            # Snapshot messages may contain multiple historical bars;
            # the freshest one (highest interval_begin) is the current
            # in-progress bar. Treat all but the freshest as already-closed
            # on snapshot.
            if msg_type == "snapshot":
                # Compare to existing inflight for this interval
                if prior is None or iv_begin > prior.get("interval_begin", ""):
                    # If we had a prior with an OLDER iv_begin, surface it as closed
                    if prior is not None and iv_begin > prior.get("interval_begin", ""):
                        self._surface_closed(interval, prior)
                    self._inflight_ohlc[interval] = entry
                # else: older snapshot bar, ignore (we already have a fresher one)
                continue
            # update messages — bar continues OR transitions
            if prior is None:
                self._inflight_ohlc[interval] = entry
                continue
            if iv_begin == prior.get("interval_begin"):
                # same bar, in-progress update
                self._inflight_ohlc[interval] = entry
                if self.on_ohlc_update:
                    try:
                        self.on_ohlc_update(entry)
                    except Exception as e:
                        log.exception("on_ohlc_update callback raised: %s", e)
            elif iv_begin > prior.get("interval_begin", ""):
                # bar transition — prior is now closed
                self._surface_closed(interval, prior)
                self._inflight_ohlc[interval] = entry
            else:
                # older iv_begin than what we have — likely out-of-order; ignore
                log.debug("ohlc out-of-order: msg iv_begin=%s < inflight=%s",
                          iv_begin, prior.get("interval_begin"))

    def _surface_closed(self, interval_min: int, closed_bar: dict):
        self._closed_bars[interval_min] = closed_bar
        log.info(
            "ohlc bar closed: interval=%dmin begin=%s close=%s",
            interval_min, closed_bar.get("interval_begin"),
            closed_bar.get("close"),
        )
        if self.on_ohlc_closed:
            try:
                self.on_ohlc_closed(closed_bar)
            except Exception as e:
                log.exception("on_ohlc_closed callback raised: %s", e)

    # ----- state transition -----

    def _set_state(self, new_state: str, notes: str = ""):
        if new_state == self.state:
            return
        old = self.state
        self.state = new_state
        log.info("ws state: %s -> %s (%s)", old, new_state, notes)
        if self.on_state_change:
            try:
                self.on_state_change(new_state, notes)
            except Exception as e:
                log.exception("on_state_change callback raised: %s", e)
