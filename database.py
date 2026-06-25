import sqlite3
import json
import logging
from datetime import datetime, date, timedelta
from config import DB_PATH, RECALL_MAX_ITEMS, RECALL_LOOKBACK_DAYS

logger = logging.getLogger(__name__)


def get_conn():
    # timeout + WAL (2026-06-12): three threads write observer.db
    # (scheduler, GateMonitor, Flask IPC) — a real 'database is locked'
    # hit gate_monitor's ws_health insert on 2026-06-09. WAL lets readers
    # and one writer overlap; journal_mode persists in the DB file, so
    # the PRAGMA is a no-op after first conversion. synchronous=NORMAL
    # is the recommended WAL pairing (corruption-safe; only the last
    # commits are at risk on power loss). The daily GCS backup uses the
    # sqlite3 .backup() API, which is WAL-safe.
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_conn()
    c = conn.cursor()

    # OHLCV candles from Coinbase
    c.execute('''CREATE TABLE IF NOT EXISTS candles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL, volume REAL,
        UNIQUE(timestamp, timeframe)
    )''')

    # Computed technical indicators
    c.execute('''CREATE TABLE IF NOT EXISTS indicators (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        ema_50 REAL, ema_200 REAL,
        adx REAL, adx_pos REAL, adx_neg REAL,
        roc_6h REAL,
        bb_width REAL, bb_upper REAL, bb_lower REAL,
        btc_ema_50 REAL, btc_ema_200 REAL,
        vwap REAL, vwap_dev_pct REAL,
        atr REAL, atr_percentile REAL,
        vol_regime TEXT,
        autocorr_1h REAL, autocorr_4h REAL,
        UNIQUE(timestamp, timeframe)
    )''')

    # Grid state
    c.execute('''CREATE TABLE IF NOT EXISTS grid_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        centre_price REAL,
        spacing_pct REAL,
        levels INTEGER,
        active INTEGER DEFAULT 1,
        pause_longs INTEGER DEFAULT 0,
        pause_shorts INTEGER DEFAULT 0,
        halt INTEGER DEFAULT 0,
        notes TEXT
    )''')

    # Grid orders
    c.execute('''CREATE TABLE IF NOT EXISTS grid_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        order_id TEXT,
        side TEXT,
        price REAL,
        size REAL,
        status TEXT,
        filled_at TEXT,
        fill_price REAL,
        fee REAL
    )''')

    # Inventory tracking
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        xrp_held REAL DEFAULT 0,
        usd_held REAL DEFAULT 0,
        net_position_usd REAL DEFAULT 0,
        inventory_skew REAL DEFAULT 0
    )''')

    # MAGI supervision decisions
    c.execute('''CREATE TABLE IF NOT EXISTS magi_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        trigger TEXT,
        melchior_action TEXT,
        melchior_conviction TEXT,
        melchior_reasoning TEXT,
        melchior_concerns TEXT,
        balthasar_action TEXT,
        balthasar_conviction TEXT,
        balthasar_reasoning TEXT,
        casper_action TEXT,
        casper_conviction TEXT,
        casper_reasoning TEXT,
        consensus_grid_action TEXT,
        consensus_risk_action TEXT,
        consensus_regime TEXT,
        applied INTEGER DEFAULT 0,
        notes TEXT
    )''')

    # Add Melchior geometry columns to magi_decisions (idempotent — wrap each
    # ALTER in try/except so re-runs are a no-op once the column exists).
    # Also adds balthasar_concerns and casper_concerns for schema symmetry
    # with melchior_concerns (the dual-write payload in orchestrator now
    # writes None for these but the columns must exist).
    for _alter in (
        "ALTER TABLE magi_decisions ADD COLUMN melchior_centre_price REAL",
        "ALTER TABLE magi_decisions ADD COLUMN melchior_target_spacing_pct REAL",
        "ALTER TABLE magi_decisions ADD COLUMN melchior_buy_level_bias REAL",
        "ALTER TABLE magi_decisions ADD COLUMN melchior_sell_level_bias REAL",
        "ALTER TABLE magi_decisions ADD COLUMN balthasar_concerns TEXT",
        "ALTER TABLE magi_decisions ADD COLUMN casper_concerns TEXT",
    ):
        try:
            c.execute(_alter)
        except sqlite3.OperationalError:
            pass  # column already exists

    # Daily P&L
    c.execute('''CREATE TABLE IF NOT EXISTS pnl_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL UNIQUE,
        gross_pnl REAL DEFAULT 0,
        fees_paid REAL DEFAULT 0,
        net_pnl REAL DEFAULT 0,
        trades_count INTEGER DEFAULT 0,
        fill_rate REAL DEFAULT 0
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS token_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        agent TEXT NOT NULL,
        model TEXT,
        prompt_tokens INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        total_tokens INTEGER DEFAULT 0,
        estimated_cost_usd REAL DEFAULT 0,
        source TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS shadow_grid_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        level_count INTEGER NOT NULL,
        spacing_pct REAL NOT NULL,
        state_blob TEXT,
        fill_count INTEGER DEFAULT 0,
        rolling_pnl_pct REAL DEFAULT 0,
        expected_pnl_pct REAL DEFAULT 0,
        updated_at TEXT,
        UNIQUE(level_count, spacing_pct)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS market_knowledge (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        computed_at TEXT NOT NULL,
        data_from TEXT,
        data_to TEXT,
        total_bars INTEGER,
        stats_json TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS supervisor_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        cycle_timestamp TEXT NOT NULL,
        council_grid_action TEXT,
        council_risk_action TEXT,
        council_regime TEXT,
        supervisor_action TEXT NOT NULL,
        override_target TEXT,
        reasoning TEXT,
        shadow_mode INTEGER DEFAULT 1,
        outcome_recorded INTEGER DEFAULT 0,
        outcome TEXT,
        outcome_notes TEXT,
        outcome_recorded_at TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS grid_config_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        config_timestamp TEXT NOT NULL,
        centre_price REAL,
        spacing_pct REAL,
        buy_level_bias REAL DEFAULT 1.0,
        sell_level_bias REAL DEFAULT 1.0,
        levels INTEGER,
        regime_at_config TEXT,
        hours_active REAL,
        fills_total INTEGER DEFAULT 0,
        fills_buy INTEGER DEFAULT 0,
        fills_sell INTEGER DEFAULT 0,
        fills_per_hour REAL DEFAULT 0.0,
        skew_start REAL,
        skew_end REAL,
        skew_delta REAL,
        gross_pnl_usd REAL DEFAULT 0.0,
        outcome_recorded_at TEXT,
        superseded_at TEXT
    )''')

    # --- Phase 5: structured debate records (one row per MAGI cycle) ---
    c.execute('''CREATE TABLE IF NOT EXISTS debate_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cycle_id TEXT UNIQUE NOT NULL,
        timestamp TEXT NOT NULL,
        trigger TEXT,

        casper_r0_position TEXT,
        casper_r0_conviction REAL,
        casper_r0_crux TEXT,
        casper_r0_evidence TEXT,

        melchior_r0_position TEXT,
        melchior_r0_conviction REAL,
        melchior_r0_crux TEXT,
        melchior_r0_evidence TEXT,

        balthasar_r0_position TEXT,
        balthasar_r0_conviction REAL,
        balthasar_r0_crux TEXT,
        balthasar_r0_evidence TEXT,

        debate_triggered INTEGER DEFAULT 0,
        conflict_pair TEXT,

        casper_r1_held INTEGER,
        melchior_r1_held INTEGER,
        balthasar_r1_held INTEGER,

        casper_revision_valid INTEGER,
        melchior_revision_valid INTEGER,
        balthasar_revision_valid INTEGER,

        casper_r1_text TEXT,
        melchior_r1_text TEXT,
        balthasar_r1_text TEXT,

        casper_r1_position TEXT,
        melchior_r1_position TEXT,

        final_grid_action TEXT,
        final_risk_action TEXT,
        deadlock INTEGER DEFAULT 0,

        applied_grid_action TEXT,
        applied_spacing REAL,
        engine_clamped INTEGER DEFAULT 0,
        clamp_reason TEXT,

        fills_1h INTEGER,
        fills_6h INTEGER,
        fills_24h INTEGER,
        pnl_1h REAL,
        pnl_6h REAL,
        pnl_24h REAL,
        unrealized_pnl_6h REAL,
        unrealized_pnl_24h REAL,
        skew_delta_6h REAL,
        grid_alive_6h INTEGER,

        outcome_1h_backfilled INTEGER DEFAULT 0,
        outcome_6h_backfilled INTEGER DEFAULT 0,
        outcome_24h_backfilled INTEGER DEFAULT 0,

        hard_rule_overrides TEXT,
        geometry_source TEXT,
        trace_id TEXT,

        config_version TEXT,
        config_snapshot TEXT,
        override_justification TEXT,

        council_json TEXT
    )''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_debate_records_cycle_id
        ON debate_records (cycle_id)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_debate_records_timestamp
        ON debate_records (timestamp)''')

    # Future-proof ALTERs for debate_records (idempotent — match the
    # try/except pattern used above for magi_decisions).
    # geometry_source: 'agent' | 'scorer_fallback' | 'unchanged'
    # Captures whether Melchior contributed geometry, the hard-rule fallback
    # injected the analytical scorer's rank-1 variant, or no geometry change
    # happened this cycle (MAINTAIN, or RECENTRE with neither agent nor
    # acceptable rank-1 available).
    for _alter in (
        "ALTER TABLE debate_records ADD COLUMN hard_rule_overrides TEXT",
        "ALTER TABLE debate_records ADD COLUMN geometry_source TEXT",
        # freshness_retries: per-agent JSON dict of whether council.py's R0
        # freshness validator forced a one-shot correction re-prompt this
        # cycle. Shape: {"casper": bool, "melchior": bool, "balthasar": bool}.
        # Populated by orchestrator from the round_0 result dicts.
        "ALTER TABLE debate_records ADD COLUMN freshness_retries TEXT",
        # world_state: full JSON snapshot of the world_state dict the council
        # was actually shown this cycle (scored_variants_top_10,
        # current_spacing_pct/current_levels, indicators, price, inventory,
        # etc.). This is the council's flight recorder. Before this column,
        # world_state was only pushed to a single Letta Cloud block that gets
        # overwritten every cycle — so no past decision's inputs were
        # recoverable and decisions like MAINTAIN-on-empty-grid could not be
        # audited after the fact. Written by orchestrator._build_debate_record.
        "ALTER TABLE debate_records ADD COLUMN world_state TEXT",
        # config_version: short hex hash of the behaviorally-relevant config that
        # produced this cycle's decision (persona hashes, per-seat served models,
        # veto mode, HARD_RULES floors, spacing/fee constants). config_snapshot:
        # the JSON of the readable components behind that hash, for forensics.
        # Added in BOTH the CREATE TABLE body (fresh DBs) and here (existing
        # observer.db) — trace_id/r1_position were CREATE-only and missed this loop;
        # not repeating that. Written by orchestrator._build_debate_record.
        "ALTER TABLE debate_records ADD COLUMN config_version TEXT",
        "ALTER TABLE debate_records ADD COLUMN config_snapshot TEXT",
        # override_justification: Stage-4 item 2a. The arbiter (Balthasar) now
        # carries the structural veto in his synthesis vote; when he PROCEEDs over a
        # live Casper regime objection (regime_action DEFER_STRUCTURAL/STAND_DOWN) on
        # a RECONFIGURE, he must justify it, and council_v2 records that prose here
        # (NULL whenever there was no such override). Added in BOTH the CREATE TABLE
        # body (fresh DBs) and here (existing observer.db). Written by
        # orchestrator._build_debate_record.
        "ALTER TABLE debate_records ADD COLUMN override_justification TEXT",
        # outcome_{w}_scores_pushed: per-window Langfuse score-delivery
        # receipt (2026-06-11). The mirror push used to be fire-and-forget at
        # backfill time — a 429/outage silently lost the scores forever (how
        # the 2026-06-10 corrected re-pushes got eaten). The observer's push
        # sweep now retries any backfilled-but-unconfirmed window every pass
        # and sets the flag only when push_trace_scores confirms delivery.
        # Same convergent pattern as seat_scores_pushed.
        "ALTER TABLE debate_records ADD COLUMN outcome_1h_scores_pushed INTEGER DEFAULT 0",
        "ALTER TABLE debate_records ADD COLUMN outcome_6h_scores_pushed INTEGER DEFAULT 0",
        "ALTER TABLE debate_records ADD COLUMN outcome_24h_scores_pushed INTEGER DEFAULT 0",
        # stance: Fix 3 (2026-06-11). The arbiter's capital mandate for the
        # cycle — DEPLOY / HOLD / STAND_ASIDE (RiskVote.stance, Balthasar
        # synthesis). Written by orchestrator._build_debate_record; read by
        # the forward-outcome stance grader and the time-in-stance
        # world_state block. NULL on pre-Fix-3 rows.
        "ALTER TABLE debate_records ADD COLUMN stance TEXT",
        # stance_correct: forward-realized grade of the stance against the
        # 72h price path (observer.backfill_stance_grades). NULL until the
        # grader runs (row not yet 72h mature, or pre-stance row); 1/0 after.
        # Thresholds are anchored to the grid band (spacing × half the level
        # count), not fitted — see the grader docstring.
        "ALTER TABLE debate_records ADD COLUMN stance_correct INTEGER",
        # stance_scores_pushed: Langfuse delivery receipt for the stance /
        # stance_correct scores — same convergent pattern as
        # outcome_{w}_scores_pushed: set to 1 only when push_trace_scores
        # confirms every POST landed (2xx); the observer sweep retries
        # unconfirmed rows every pass. NULL-trace rows are stamped 1 with
        # nothing to deliver.
        "ALTER TABLE debate_records ADD COLUMN stance_scores_pushed INTEGER DEFAULT 0",
        # council_json: the blind-review council's OWN memory (redesign 2026-06-24).
        # One JSON object per cycle: {decision, vote_multiset (authorship-free, e.g.
        # "2x MAINTAIN, 1x RECONFIGURE"), consensus ("clear"|"reconciled"|"none"),
        # reconciled (bool)}. The matured outcome (fills/pnl) is filled later by the
        # existing backfill columns; get_council_ledger joins the two for recall.
        # NULL on pre-redesign rows (the arbiter relay wrote no council_json) — those
        # rows are not migrated. Written by orchestrator._build_debate_record.
        "ALTER TABLE debate_records ADD COLUMN council_json TEXT",
        # Per-seat RAW proposed action (blind-review redesign) — the lossless record
        # the symmetric forward-realized seat grader reads (the *_r0_position columns
        # hold lossy verdict/risk projections). NOT injected into the council (kept
        # out of council_json, which is the authorship-free ledger), so grading
        # authorship can't leak back into the blind review. NULL on a non-responding
        # seat and on every arbiter-era row. Written by _build_debate_record.
        "ALTER TABLE debate_records ADD COLUMN casper_r0_action TEXT",
        "ALTER TABLE debate_records ADD COLUMN melchior_r0_action TEXT",
        "ALTER TABLE debate_records ADD COLUMN balthasar_r0_action TEXT",
    ):
        try:
            c.execute(_alter)
        except sqlite3.OperationalError:
            pass

    # --- Alerts surfaced to the dashboard ---
    # Categories: credit_exhausted / auth_failed / rate_limited
    #           / provider_error / unknown_failure / test
    # Severity: info / warn / critical
    # provider_category: 'base' (Letta-managed) or 'byok'
    # provider_name: anthropic / openai / google_ai / BATHY / GEEP / GEMMNY / ...
    # step_id: Letta Step.id for traceback / sweep dedup
    c.execute('''CREATE TABLE IF NOT EXISTS magi_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        severity TEXT NOT NULL,
        category TEXT NOT NULL,
        agent_id TEXT,
        provider_category TEXT,
        provider_name TEXT,
        step_id TEXT,
        message TEXT NOT NULL,
        resolved INTEGER DEFAULT 0,
        resolved_at TEXT
    )''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_magi_alerts_open
        ON magi_alerts (resolved, timestamp)''')
    # Idempotent ALTERs for pre-existing tables (this session's earlier draft
    # had a leaner schema; bring it forward).
    for _alter in (
        "ALTER TABLE magi_alerts ADD COLUMN provider_category TEXT",
        "ALTER TABLE magi_alerts ADD COLUMN provider_name TEXT",
        "ALTER TABLE magi_alerts ADD COLUMN step_id TEXT",
    ):
        try:
            c.execute(_alter)
        except sqlite3.OperationalError:
            pass
    # step_id index after the ALTER so it succeeds on pre-existing tables.
    c.execute('''CREATE INDEX IF NOT EXISTS idx_magi_alerts_step_id
        ON magi_alerts (step_id)''')

    # --- Letta Evals: per-run results (one row per agent per suite execution) ---
    c.execute('''CREATE TABLE IF NOT EXISTS magi_eval_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        suite_name TEXT NOT NULL,
        total_samples INTEGER NOT NULL,
        passed_samples INTEGER NOT NULL,
        accuracy REAL NOT NULL,
        gate_passed INTEGER NOT NULL,
        gate_threshold REAL NOT NULL,
        cost_usd_estimate REAL,
        raw_results_path TEXT,
        git_commit_sha TEXT,
        notes TEXT
    )''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_eval_runs_agent_ts
        ON magi_eval_runs (agent_id, timestamp)''')

    # --- Phase 5: Letta agent registry (logical agent ↔ Letta UUID) ---
    c.execute('''CREATE TABLE IF NOT EXISTS agent_registry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id TEXT UNIQUE NOT NULL,
        letta_agent_id TEXT NOT NULL,
        shared_world_block_id TEXT,
        shared_peer_block_ids TEXT,
        model TEXT,
        created_at TEXT NOT NULL,
        last_active TEXT
    )''')

    for _alter in (
        # placeholder; e.g. "ALTER TABLE agent_registry ADD COLUMN notes TEXT"
    ):
        try:
            c.execute(_alter)
        except sqlite3.OperationalError:
            pass

    # --- Memory rotation lifecycle ---
    # One row per agent per rotation attempt (success OR failure). Status
    # vocabulary matches magi.memory_lifecycle.rotate_agent_memory:
    #   success / validation_failed / merge_failed / snapshot_failed
    #   / compact_failed / skipped / skipped_degraded / error
    # degraded_count_in_window: # of last-30 R0 rows for this agent that
    # matched SAFE_DEFAULTS (conviction=0, crux=(no response)). Populated
    # on every rotation attempt; the pre-gate skips when >= 12/30 (40%).
    c.execute('''CREATE TABLE IF NOT EXISTS memory_rotations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        cycle_number INTEGER NOT NULL,
        self_model_chars_before INTEGER,
        self_model_chars_after INTEGER,
        patterns_added INTEGER,
        status TEXT NOT NULL,
        snapshot_path TEXT,
        error_detail TEXT,
        degraded_count_in_window INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_memory_rotations_agent_ts
        ON memory_rotations (agent_id, timestamp)''')
    # Idempotent ALTER for pre-existing DBs (this column was added 2026-05-20).
    for _alter in (
        "ALTER TABLE memory_rotations ADD COLUMN degraded_count_in_window INTEGER DEFAULT 0",
    ):
        try:
            c.execute(_alter)
        except sqlite3.OperationalError:
            pass

    # --- Generic system state (key/value) ---
    # Used for cross-restart counters that need to survive a scheduler
    # restart. First user: rotation_cycle_counter (driven by scheduler.py;
    # consumed by magi.memory_lifecycle.maybe_rotate).
    c.execute('''CREATE TABLE IF NOT EXISTS system_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )''')

    # Gate layer: trip-wire events from magi/gate.py. One row per
    # trigger evaluated per observer poll (both fired and quiet rows);
    # the orchestrator reads unconsumed rows (consumed_in_cycle IS NULL)
    # at build_world_state time and surfaces fired events to the
    # council. consumed_in_cycle is set to the cycle_id after the cycle's
    # debate_records row commits, so the same trigger does not appear
    # in the next cycle's window.
    c.execute('''CREATE TABLE IF NOT EXISTS magi_gate_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        trigger_id TEXT NOT NULL,
        fired INTEGER NOT NULL,
        details TEXT,
        consumed_in_cycle TEXT
    )''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_gate_events_timestamp
        ON magi_gate_events(timestamp)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_gate_events_unconsumed
        ON magi_gate_events(consumed_in_cycle)
        WHERE consumed_in_cycle IS NULL''')

    # Council two-stage synthesis vote fields (added with the always-R1
    # synthesis architecture). Idempotent ALTERs — first run adds the
    # columns; subsequent runs noop the ALTER and silently skip.
    #   regime_action ∈ {EXECUTE, DEFER_STRUCTURAL, STAND_DOWN}
    #   geometry_veto ∈ {PROCEED, HOLD_GEOMETRY, RISK_BLOCK}
    # Default-on-missing is permissive: EXECUTE / PROCEED. The engine
    # downgrades grid_action to MAINTAIN when either is non-permissive.
    for migration in (
        "ALTER TABLE debate_records ADD COLUMN regime_action TEXT",
        "ALTER TABLE debate_records ADD COLUMN geometry_veto TEXT",
        # trace_id: Langfuse trace id for this cycle's council debate. Groundwork
        # for Stage 3 — no writer yet, stays NULL until the orchestrator stamps it
        # (magi/agents/tracing.py:current_trace_id). One trace per cycle.
        "ALTER TABLE debate_records ADD COLUMN trace_id TEXT",
        # unrealized_pnl_{6h,24h}: change in total mark-to-market position value
        # over the forward window (window-end value − decision-time baseline),
        # written alongside the realized pnl_{6h,24h} by the observer backfill.
        # Same live-only basis as realized; inert (0.0 fallback) during paper.
        "ALTER TABLE debate_records ADD COLUMN unrealized_pnl_6h REAL",
        "ALTER TABLE debate_records ADD COLUMN unrealized_pnl_24h REAL",
        # casper_r1_position / melchior_r1_position: each agent's FINAL
        # POST-REBUTTAL structured label (Casper regime, Melchior verdict) from
        # council_v2's rebuttal round. ALWAYS written with the agent's final call —
        # whether it revised or held — because the rebuttal re-emits a full vote
        # each round, so accuracy scoring can read the post-rebuttal label directly.
        # Whether the agent HELD vs REVISED is read from the separate {agent}_r1_held
        # flag (1 = held, 0 = revised), not from a NULL here. NULL only when the
        # rebuttal call failed to parse or round_1 is absent. There is deliberately
        # NO balthasar_r1_position — Balthasar is the arbiter: his post-rebuttal call
        # is the synthesis, recorded as final_risk_action, not a rebuttal label.
        "ALTER TABLE debate_records ADD COLUMN casper_r1_position TEXT",
        "ALTER TABLE debate_records ADD COLUMN melchior_r1_position TEXT",
    ):
        try:
            c.execute(migration)
        except sqlite3.OperationalError:
            pass  # already exists

    # WebSocket health metrics for the always-on gate_monitor service.
    # One row written every state transition + a heartbeat row every ~5s
    # while connected. Dashboard reads MAX(id) for the live chip.
    # state ∈ {'connected','reconnecting','degraded','disconnected','starting'}.
    # 'degraded' means WS down + REST fallback active. 'reconnecting'
    # means WS down + actively retrying with backoff.
    c.execute('''CREATE TABLE IF NOT EXISTS ws_health (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        state TEXT NOT NULL,
        last_heartbeat_age_sec REAL,
        reconnect_count_1h INTEGER,
        last_tick_age_sec REAL,
        notes TEXT
    )''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_ws_health_timestamp
        ON ws_health(timestamp)''')

    conn.commit()
    conn.close()
    print("Database initialised.")


# --- Candle helpers ---

def insert_candle(timestamp, timeframe, o, h, l, c_price, volume):
    conn = get_conn()
    try:
        conn.execute('''INSERT INTO candles
            (timestamp, timeframe, open, high, low, close, volume)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(timestamp, timeframe) DO UPDATE SET
                high   = MAX(excluded.high,  candles.high),
                low    = MIN(excluded.low,   candles.low),
                close  = excluded.close,
                volume = excluded.volume''',
            (timestamp, timeframe, o, h, l, c_price, volume))
        conn.commit()
    finally:
        conn.close()


def get_candles(timeframe, limit=500):
    conn = get_conn()
    rows = conn.execute('''SELECT * FROM candles WHERE timeframe=?
        ORDER BY timestamp DESC LIMIT ?''', (timeframe, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_candle_hl(timeframe='1h'):
    """Return (high, low) of the most recent COMPLETED candle, or (None, None)
    if no completed candle exists. A candle is completed once its timestamp
    is strictly before the top of the current UTC hour — selecting by highest
    id would otherwise return the in-progress candle, whose high/low only
    cover the first few minutes of the current hour and silently mask
    mid-hour fills."""
    current_hour_start = datetime.utcnow().replace(
        minute=0, second=0, microsecond=0
    ).isoformat()
    conn = get_conn()
    row = conn.execute(
        '''SELECT high, low FROM candles
           WHERE timeframe=? AND timestamp < ?
           ORDER BY timestamp DESC LIMIT 1''',
        (timeframe, current_hour_start)
    ).fetchone()
    conn.close()
    if row:
        return float(row['high']), float(row['low'])
    return None, None


# --- Indicator helpers ---

def upsert_indicators(timestamp, timeframe, data: dict):
    conn = get_conn()
    fields = ', '.join(data.keys())
    placeholders = ', '.join(['?' for _ in data])
    updates = ', '.join([f"{k}=excluded.{k}" for k in data.keys()])
    values = list(data.values())
    conn.execute(f'''INSERT INTO indicators (timestamp, timeframe, {fields})
        VALUES (?, ?, {placeholders})
        ON CONFLICT(timestamp, timeframe) DO UPDATE SET {updates}''',
        [timestamp, timeframe] + values)
    conn.commit()
    conn.close()


def get_latest_indicators(timeframe='1h'):
    conn = get_conn()
    row = conn.execute('''SELECT * FROM indicators WHERE timeframe=?
        ORDER BY id DESC LIMIT 1''', (timeframe,)).fetchone()
    conn.close()
    return dict(row) if row else None


# --- Grid state helpers ---

def insert_grid_state(centre_price, spacing_pct, levels, pause_longs=0, pause_shorts=0, notes=None):
    conn = get_conn()
    conn.execute('''INSERT INTO grid_state
        (timestamp, centre_price, spacing_pct, levels, pause_longs, pause_shorts, notes)
        VALUES (?,?,?,?,?,?,?)''',
        (datetime.utcnow().isoformat(), centre_price, spacing_pct, levels,
         pause_longs, pause_shorts, notes))
    conn.commit()
    conn.close()


def get_current_grid_state():
    conn = get_conn()
    row = conn.execute('''SELECT * FROM grid_state
        ORDER BY timestamp DESC LIMIT 1''').fetchone()
    conn.close()
    return dict(row) if row else None


# --- Grid order helpers ---

def insert_grid_order(timestamp, order_id, side, price, size, status,
                      fee=0.0, filled_at=None, fill_price=None):
    conn = get_conn()
    conn.execute('''INSERT INTO grid_orders
        (timestamp, order_id, side, price, size, status, fee, filled_at, fill_price)
        VALUES (?,?,?,?,?,?,?,?,?)''',
        (timestamp, order_id, side, price, size, status,
         fee, filled_at, fill_price))
    conn.commit()
    conn.close()


def get_gate_trigger_stats(window_hours=168):
    """Per-trigger gate fire-rate over the trailing window, for monitoring
    whether thresholds are too loose (firing constantly → needless wakes) or
    too tight (never firing → missed depletion). Reads magi_gate_events
    (one row per trigger per eval). Excludes the *_eval bookkeeping rows.

    Returns:
      {
        'window_hours': int,
        'generated_at_utc': iso,
        'triggers': [ {trigger_id, evals, fires_window, fires_24h,
                       last_fired_ts (unix), last_fired_details (json str)} ],
        'wakes': {  # off-schedule MAGI cycles caused by gate wakes
            'window': int, 'last_24h': int, 'by_trigger': {gate_wake:Tn: count} }
      }
    """
    import time as _t
    from datetime import datetime, timedelta, timezone

    conn = get_conn()
    try:
        now = _t.time()
        cutoff = now - window_hours * 3600
        cutoff_24 = now - 24 * 3600

        rows = conn.execute(
            "SELECT trigger_id, SUM(fired) AS fires, COUNT(*) AS evals, "
            "MAX(CASE WHEN fired=1 THEN timestamp END) AS last_fired_ts "
            "FROM magi_gate_events WHERE timestamp >= ? "
            "GROUP BY trigger_id",
            (cutoff,),
        ).fetchall()
        rows24 = conn.execute(
            "SELECT trigger_id, SUM(fired) AS f FROM magi_gate_events "
            "WHERE timestamp >= ? GROUP BY trigger_id",
            (cutoff_24,),
        ).fetchall()
        fires24 = {r['trigger_id']: (r['f'] or 0) for r in rows24}

        triggers = []
        for r in rows:
            tid = r['trigger_id']
            if tid.endswith('_eval'):   # bookkeeping/edge-state rows, not real fires
                continue
            details = None
            if r['last_fired_ts']:
                drow = conn.execute(
                    "SELECT details FROM magi_gate_events "
                    "WHERE trigger_id=? AND fired=1 ORDER BY id DESC LIMIT 1",
                    (tid,),
                ).fetchone()
                details = drow['details'] if drow else None
            triggers.append({
                'trigger_id': tid,
                'evals': r['evals'] or 0,
                'fires_window': r['fires'] or 0,
                'fires_24h': fires24.get(tid, 0),
                'last_fired_ts': r['last_fired_ts'],
                'last_fired_details': details,
            })
        triggers.sort(key=lambda t: t['trigger_id'])

        # Off-schedule wakes: debate_records.trigger LIKE 'gate_wake:%'
        # (timestamp is ISO text there, not unix).
        iso_cut = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        iso_cut_24 = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        wake_rows = conn.execute(
            "SELECT trigger, COUNT(*) AS n FROM debate_records "
            "WHERE trigger LIKE 'gate_wake:%' AND timestamp >= ? GROUP BY trigger",
            (iso_cut,),
        ).fetchall()
        wake_24 = conn.execute(
            "SELECT COUNT(*) AS n FROM debate_records "
            "WHERE trigger LIKE 'gate_wake:%' AND timestamp >= ?",
            (iso_cut_24,),
        ).fetchone()
        by_trigger = {r['trigger']: r['n'] for r in wake_rows}

        return {
            'window_hours': window_hours,
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'triggers': triggers,
            'wakes': {
                'window': sum(by_trigger.values()),
                'last_24h': (wake_24['n'] if wake_24 else 0),
                'by_trigger': by_trigger,
            },
        }
    finally:
        conn.close()


def update_grid_order_status(order_id, status,
                              filled_at=None, fill_price=None, fee=None):
    conn = get_conn()
    sets = ['status=?']
    vals = [status]
    if filled_at is not None:
        sets.append('filled_at=?')
        vals.append(filled_at)
    if fill_price is not None:
        sets.append('fill_price=?')
        vals.append(fill_price)
    if fee is not None:
        sets.append('fee=?')
        vals.append(fee)
    vals.append(order_id)
    conn.execute(f"UPDATE grid_orders SET {', '.join(sets)} WHERE order_id=?", vals)
    conn.commit()
    conn.close()


def get_recent_grid_orders(limit=50):
    conn = get_conn()
    rows = conn.execute('''SELECT * FROM grid_orders
        ORDER BY timestamp DESC LIMIT ?''', (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_open_orders_summary():
    """Return open order counts, prices, and recent fills for agent context."""
    from datetime import timedelta
    conn = get_conn()

    open_rows = conn.execute(
        "SELECT side, price, size FROM grid_orders WHERE status='open' "
        "ORDER BY price ASC"
    ).fetchall()

    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    fill_rows = conn.execute(
        "SELECT side, fill_price, price, size, filled_at FROM grid_orders "
        "WHERE status='filled' AND filled_at >= ? "
        "ORDER BY filled_at DESC LIMIT 10",
        (cutoff,)
    ).fetchall()

    conn.close()

    buys  = [dict(r) for r in open_rows if r['side'] == 'buy']
    sells = [dict(r) for r in open_rows if r['side'] == 'sell']
    fills = [dict(r) for r in fill_rows]

    return {
        'open_buys':     buys,
        'open_sells':    sells,
        'recent_fills':  fills,
        'buy_count':     len(buys),
        'sell_count':    len(sells),
        'highest_buy':   max((b['price'] for b in buys),  default=None),
        'lowest_sell':   min((s['price'] for s in sells), default=None),
    }


def get_trajectory_context():
    """
    Compute trajectory and positional context from recent history.
    Returns a dict of derived metrics for agent context injection.
    All values gracefully degrade to None if insufficient history exists.
    """
    from datetime import timedelta
    conn = get_conn()

    # Last 5 MAGI decisions for trajectory
    decisions = conn.execute(
        "SELECT timestamp, melchior_action, balthasar_action, "
        "casper_action, consensus_risk_action, consensus_grid_action "
        "FROM magi_decisions ORDER BY timestamp DESC LIMIT 5"
    ).fetchall()

    # Last 5 inventory snapshots for skew trajectory
    inv_rows = conn.execute(
        "SELECT timestamp, inventory_skew FROM inventory "
        "ORDER BY timestamp DESC LIMIT 5"
    ).fetchall()

    # Fills since last MAGI cycle
    last_decision_ts = decisions[0]['timestamp'] if decisions else None
    if last_decision_ts:
        fills = conn.execute(
            "SELECT side, COUNT(*) as count FROM grid_orders "
            "WHERE status='filled' AND filled_at >= ? "
            "GROUP BY side",
            (last_decision_ts,)
        ).fetchall()
    else:
        fills = []

    # Current grid state pause flags
    grid_row = conn.execute(
        "SELECT pause_longs, pause_shorts, timestamp FROM grid_state "
        "ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()

    conn.close()

    # Compute derived metrics
    result = {
        'regime_consecutive': None,
        'melchior_blocked_cycles': None,
        'skew_delta': None,
        'skew_trend': None,
        'fills_since_last_magi_buys': 0,
        'fills_since_last_magi_sells': 0,
        'cycles_since_structural_change': None,
        'pause_longs_active': 0,
        'pause_shorts_active': 0,
        # fills_per_hour: derived from fills_since_last_magi_{buys,sells}
        # divided by hours since the last MAGI decision. Defaults to 0.0
        # when no prior decision exists (first cycle on a fresh DB).
        # Consumed by Melchior's Step 3 MID-band gate per
        # magi/world_state_schema.py.
        'fills_per_hour': 0.0,
    }

    if grid_row:
        result['pause_longs_active'] = grid_row['pause_longs'] or 0
        result['pause_shorts_active'] = grid_row['pause_shorts'] or 0

    for f in fills:
        if f['side'] == 'buy':
            result['fills_since_last_magi_buys'] = f['count']
        elif f['side'] == 'sell':
            result['fills_since_last_magi_sells'] = f['count']

    if len(inv_rows) >= 2:
        current_skew = inv_rows[0]['inventory_skew'] or 0
        prior_skew = inv_rows[1]['inventory_skew'] or 0
        result['skew_delta'] = round(current_skew - prior_skew, 4)
        if len(inv_rows) >= 3:
            oldest_skew = inv_rows[-1]['inventory_skew'] or 0
            if current_skew > oldest_skew + 0.05:
                result['skew_trend'] = 'worsening_long'
            elif current_skew < oldest_skew - 0.05:
                result['skew_trend'] = 'worsening_short'
            else:
                result['skew_trend'] = 'stable'

    if decisions:
        # How many consecutive cycles has Casper called the same regime
        current_regime = decisions[0]['casper_action']
        count = 0
        for d in decisions:
            if d['casper_action'] == current_regime:
                count += 1
            else:
                break
        result['regime_consecutive'] = count

        # How many consecutive cycles has Melchior's recommendation been blocked
        # (grid action was MAINTAIN but Melchior didn't say MAINTAIN)
        blocked = 0
        for d in decisions:
            if (d['consensus_grid_action'] == 'MAINTAIN' and
                    d['melchior_action'] != 'MAINTAIN'):
                blocked += 1
            else:
                break
        result['melchior_blocked_cycles'] = blocked

        # How many consecutive cycles since grid structure actually changed
        stable = 0
        for d in decisions:
            if d['consensus_grid_action'] == 'MAINTAIN':
                stable += 1
            else:
                break
        result['cycles_since_structural_change'] = stable

        # fills_per_hour — total fills (buys+sells) since the last MAGI
        # decision divided by hours since that decision. Floor of 0.01h
        # avoids div-by-zero on rapid re-fires.
        try:
            last_dt = datetime.fromisoformat(last_decision_ts)
            hours_since = (datetime.utcnow() - last_dt).total_seconds() / 3600.0
            hours_since = max(hours_since, 0.01)
            total_fills = (
                int(result['fills_since_last_magi_buys'] or 0)
                + int(result['fills_since_last_magi_sells'] or 0)
            )
            result['fills_per_hour'] = round(total_fills / hours_since, 4)
        except (ValueError, TypeError):
            result['fills_per_hour'] = 0.0

    return result


def get_fills_today_count():
    conn = get_conn()
    today = date.today().isoformat()
    row = conn.execute('''SELECT COUNT(*) as cnt FROM grid_orders
        WHERE status='filled' AND (filled_at >= ? OR timestamp >= ?)''',
        (today, today)).fetchone()
    conn.close()
    return row['cnt'] if row else 0


# --- MAGI decision helpers ---

def insert_magi_decision(data: dict):
    conn = get_conn()
    data['timestamp'] = datetime.utcnow().isoformat()
    fields = ', '.join(data.keys())
    placeholders = ', '.join(['?' for _ in data])
    conn.execute(f'INSERT INTO magi_decisions ({fields}) VALUES ({placeholders})',
        list(data.values()))
    conn.commit()
    conn.close()


def get_latest_magi_decision_id():
    conn = get_conn()
    row = conn.execute(
        'SELECT id FROM magi_decisions ORDER BY id DESC LIMIT 1'
    ).fetchone()
    conn.close()
    return row['id'] if row else None


def mark_magi_decision_applied(decision_id):
    conn = get_conn()
    conn.execute(
        'UPDATE magi_decisions SET applied=1 WHERE id=?',
        (decision_id,)
    )
    conn.commit()
    conn.close()


# Source of truth: Phase 5 writes to debate_records (canonical) AND
# dual-writes to magi_decisions for legacy readers (dashboard hard-rule tag
# parser, learning.py, extract_test_cases.py, scheduler startup-debounce).
# Use debate_records for new code; reuse this helper only when you need the
# legacy column shape (e.g. .notes, .applied) the dashboard parses.
def get_recent_magi_decisions(limit=10):
    conn = get_conn()
    rows = conn.execute('''SELECT * FROM magi_decisions
        ORDER BY timestamp DESC LIMIT ?''', (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Inventory helpers ---

def upsert_inventory(xrp_held, usd_held, net_position_usd, inventory_skew):
    conn = get_conn()
    conn.execute('''INSERT INTO inventory
        (timestamp, xrp_held, usd_held, net_position_usd, inventory_skew)
        VALUES (?,?,?,?,?)''',
        (datetime.utcnow().isoformat(), xrp_held, usd_held,
         net_position_usd, inventory_skew))
    conn.commit()
    conn.close()


def get_latest_inventory():
    conn = get_conn()
    row = conn.execute('''SELECT * FROM inventory
        ORDER BY id DESC LIMIT 1''').fetchone()
    conn.close()
    return dict(row) if row else None


# --- Shadow grid helpers ---

def upsert_shadow_grid_state(level_count, spacing_pct, state_dict,
                              fill_count=0, rolling_pnl_pct=0.0,
                              expected_pnl_pct=0.0):
    conn = get_conn()
    conn.execute('''INSERT INTO shadow_grid_state
        (level_count, spacing_pct, state_blob, fill_count,
         rolling_pnl_pct, expected_pnl_pct, updated_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(level_count, spacing_pct) DO UPDATE SET
            state_blob=excluded.state_blob,
            fill_count=excluded.fill_count,
            rolling_pnl_pct=excluded.rolling_pnl_pct,
            expected_pnl_pct=excluded.expected_pnl_pct,
            updated_at=excluded.updated_at''',
        (level_count, spacing_pct, json.dumps(state_dict),
         fill_count, rolling_pnl_pct, expected_pnl_pct,
         datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_shadow_grid_state(level_count, spacing_pct):
    conn = get_conn()
    row = conn.execute(
        'SELECT state_blob FROM shadow_grid_state '
        'WHERE level_count=? AND spacing_pct=?',
        (level_count, spacing_pct)).fetchone()
    conn.close()
    if row and row['state_blob']:
        return json.loads(row['state_blob'])
    return None


def get_active_shadow_level_count() -> int | None:
    """Return the level_count currently in use by the live grid,
    from the grid_state table. This is the authoritative source —
    shadow_grid_state.updated_at is not reliable because persist_all()
    always writes all variants, making the last-updated variant
    arbitrary."""
    conn = get_conn()
    row = conn.execute(
        '''SELECT levels FROM grid_state
           ORDER BY timestamp DESC LIMIT 1'''
    ).fetchone()
    conn.close()
    return int(row['levels']) if row and row['levels'] else None


def get_all_shadow_states():
    conn = get_conn()
    rows = conn.execute(
        '''SELECT level_count, spacing_pct, fill_count, rolling_pnl_pct,
                  expected_pnl_pct, updated_at
           FROM shadow_grid_state
           ORDER BY level_count, spacing_pct'''
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_best_shadow_from_db():
    """Return (level_count, spacing_pct, rolling_pnl_pct) for the best shadow
    variant with fills > 0. Returns (None, None, None) if no variant has
    fills."""
    rows = get_all_shadow_states()
    candidates = [r for r in rows if (r['fill_count'] or 0) > 0]
    if not candidates:
        return None, None, None
    best = max(candidates, key=lambda r: r['rolling_pnl_pct'] or 0)
    return best['level_count'], best['spacing_pct'], best['rolling_pnl_pct']


# --- Token usage helpers ---

def insert_token_usage(agent, model, prompt_tokens, completion_tokens, total_tokens, cost_usd, source='direct'):
    conn = get_conn()
    conn.execute('''INSERT INTO token_usage
        (timestamp, agent, model, prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd, source)
        VALUES (?,?,?,?,?,?,?,?)''',
        (datetime.utcnow().isoformat(), agent, model, prompt_tokens,
         completion_tokens, total_tokens, cost_usd, source))
    conn.commit()
    conn.close()


def get_cost_summary(days_back=30):
    from datetime import timedelta
    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(days=days_back)).isoformat()
    # Display model comes from agent_registry (live source of truth). Fall
    # back to MAX(token_usage.model) when an agent has no registry row
    # (e.g. one-off / pre-registry rows). One row per agent so legacy
    # model strings collapse into the current label.
    rows = conn.execute('''SELECT tu.agent AS agent,
        COALESCE(ar.model, MAX(tu.model)) AS model,
        SUM(tu.prompt_tokens) as prompt_tokens,
        SUM(tu.completion_tokens) as completion_tokens,
        SUM(tu.total_tokens) as total_tokens,
        SUM(tu.estimated_cost_usd) as cost,
        COUNT(*) as calls
        FROM token_usage tu
        LEFT JOIN agent_registry ar ON ar.agent_id = tu.agent
        WHERE tu.timestamp > ?
        GROUP BY tu.agent''', (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_cost_today():
    conn = get_conn()
    today = date.today().isoformat()
    row = conn.execute('''SELECT
        SUM(estimated_cost_usd) as cost,
        SUM(total_tokens) as tokens,
        COUNT(*) as calls
        FROM token_usage WHERE timestamp > ?''', (today,)).fetchone()
    conn.close()
    return dict(row) if row else {'cost': 0, 'tokens': 0, 'calls': 0}


def ensure_market_knowledge_table():
    """Create market_knowledge table if it does not exist."""
    conn = get_conn()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS market_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            computed_at TEXT NOT NULL,
            data_from TEXT,
            data_to TEXT,
            total_bars INTEGER,
            stats_json TEXT
        )
    ''')
    conn.commit()
    conn.close()


# --- Supervisor decision helpers ---

def insert_supervisor_decision(cycle_timestamp, council_grid_action,
                                council_risk_action, council_regime,
                                supervisor_action, override_target,
                                reasoning, shadow_mode=1):
    conn = get_conn()
    conn.execute('''INSERT INTO supervisor_decisions
        (timestamp, cycle_timestamp, council_grid_action, council_risk_action,
         council_regime, supervisor_action, override_target, reasoning, shadow_mode)
        VALUES (?,?,?,?,?,?,?,?,?)''',
        (datetime.utcnow().isoformat(), cycle_timestamp, council_grid_action,
         council_risk_action, council_regime, supervisor_action,
         override_target, reasoning, shadow_mode))
    conn.commit()
    conn.close()


def record_supervisor_outcome(decision_id, outcome, outcome_notes):
    conn = get_conn()
    conn.execute('''UPDATE supervisor_decisions
        SET outcome=?, outcome_notes=?, outcome_recorded=1,
            outcome_recorded_at=?
        WHERE id=?''',
        (outcome, outcome_notes, datetime.utcnow().isoformat(), decision_id))
    conn.commit()
    conn.close()


def get_pending_outcome_decisions(hours_threshold=6):
    """Return supervisor decisions that need outcome recording."""
    from datetime import timedelta
    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(hours=hours_threshold)).isoformat()
    rows = conn.execute('''SELECT id, timestamp, supervisor_action,
                                  override_target, council_grid_action
                           FROM supervisor_decisions
                           WHERE outcome_recorded=0
                             AND timestamp < ?
                             AND shadow_mode=0''',
                        (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Grid config outcomes (Melchior performance feedback) ---

def record_grid_config(centre_price, spacing_pct, buy_level_bias,
                        sell_level_bias, levels, regime_at_config,
                        skew_start):
    """
    Called when a new grid is initialised. Records the config
    so outcomes can be written later.
    """
    conn = get_conn()
    conn.execute(
        "UPDATE grid_config_outcomes SET superseded_at=? "
        "WHERE superseded_at IS NULL",
        (datetime.utcnow().isoformat(),)
    )
    conn.execute(
        '''INSERT INTO grid_config_outcomes
           (config_timestamp, centre_price, spacing_pct,
            buy_level_bias, sell_level_bias, levels,
            regime_at_config, skew_start)
           VALUES (?,?,?,?,?,?,?,?)''',
        (datetime.utcnow().isoformat(), centre_price, spacing_pct,
         buy_level_bias, sell_level_bias, levels,
         regime_at_config, skew_start)
    )
    conn.commit()
    conn.close()


def update_grid_config_outcome(min_hours_active=2.0):
    """
    Called from observer cycle. Finds the active config
    (superseded_at IS NULL), computes outcomes from fills
    and inventory since config_timestamp, writes back.
    Only updates if config has been active for min_hours_active.
    """
    conn = get_conn()

    active = conn.execute(
        "SELECT id, config_timestamp, skew_start "
        "FROM grid_config_outcomes "
        "WHERE superseded_at IS NULL "
        "ORDER BY config_timestamp DESC LIMIT 1"
    ).fetchone()

    if not active:
        conn.close()
        return

    config_id, config_ts, skew_start = active['id'], active['config_timestamp'], active['skew_start']

    try:
        config_dt = datetime.fromisoformat(config_ts)
        hours_active = (datetime.utcnow() - config_dt).total_seconds() / 3600
    except Exception:
        conn.close()
        return

    if hours_active < min_hours_active:
        conn.close()
        return

    fills = conn.execute(
        """SELECT
            COUNT(*) as total,
            SUM(CASE WHEN side='buy' THEN 1 ELSE 0 END) as buys,
            SUM(CASE WHEN side='sell' THEN 1 ELSE 0 END) as sells
           FROM grid_orders
           WHERE status='filled' AND filled_at >= ?""",
        (config_ts,)
    ).fetchone()

    fills_total = fills['total'] or 0
    fills_buy = fills['buys'] or 0
    fills_sell = fills['sells'] or 0
    fills_per_hour = fills_total / hours_active if hours_active > 0 else 0.0

    inv = conn.execute(
        "SELECT inventory_skew FROM inventory "
        "ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    skew_end = inv['inventory_skew'] if inv else skew_start
    skew_delta = (skew_end - skew_start) if (skew_start is not None and skew_end is not None) else 0.0

    conn.execute(
        """UPDATE grid_config_outcomes SET
            hours_active=?, fills_total=?, fills_buy=?,
            fills_sell=?, fills_per_hour=?,
            skew_end=?, skew_delta=?,
            outcome_recorded_at=?
           WHERE id=?""",
        (hours_active, fills_total, fills_buy, fills_sell,
         fills_per_hour, skew_end, skew_delta,
         datetime.utcnow().isoformat(), config_id)
    )
    conn.commit()
    conn.close()


def get_recent_grid_config_outcomes(n=5):
    """
    Returns last N completed grid configs with outcomes.
    Used to build Melchior's feedback context.
    """
    conn = get_conn()
    rows = conn.execute(
        """SELECT config_timestamp, centre_price, spacing_pct,
                  buy_level_bias, sell_level_bias, levels,
                  regime_at_config, hours_active, fills_total,
                  fills_per_hour, skew_start, skew_end, skew_delta
           FROM grid_config_outcomes
           WHERE outcome_recorded_at IS NOT NULL
             AND hours_active IS NOT NULL
           ORDER BY config_timestamp DESC
           LIMIT ?""",
        (n,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Phase 5: agent registry helpers ---

_VALID_AGENT_IDS = ('casper', 'melchior', 'balthasar')
_VALID_WINDOWS = ('1h', '6h', '24h')


def register_agent(agent_id, letta_agent_id, model,
                    shared_world_block_id=None, shared_peer_block_ids=None):
    """
    Upsert a logical agent ↔ Letta UUID mapping. shared_peer_block_ids
    may be a list/dict (JSON-serialised) or already-serialised string.
    Updates last_active on every call; created_at is preserved on update.
    """
    if isinstance(shared_peer_block_ids, (list, dict)):
        shared_peer_block_ids = json.dumps(shared_peer_block_ids)
    now = datetime.utcnow().isoformat()
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM agent_registry WHERE agent_id=?", (agent_id,)
    ).fetchone()
    if existing:
        conn.execute(
            '''UPDATE agent_registry
               SET letta_agent_id=?, shared_world_block_id=?,
                   shared_peer_block_ids=?, model=?, last_active=?
               WHERE agent_id=?''',
            (letta_agent_id, shared_world_block_id, shared_peer_block_ids,
             model, now, agent_id)
        )
    else:
        conn.execute(
            '''INSERT INTO agent_registry
               (agent_id, letta_agent_id, shared_world_block_id,
                shared_peer_block_ids, model, created_at, last_active)
               VALUES (?,?,?,?,?,?,?)''',
            (agent_id, letta_agent_id, shared_world_block_id,
             shared_peer_block_ids, model, now, now)
        )
    conn.commit()
    conn.close()


def get_letta_agent_id(agent_id):
    """Return the Letta UUID for the given logical agent, or None."""
    conn = get_conn()
    row = conn.execute(
        "SELECT letta_agent_id FROM agent_registry WHERE agent_id=?",
        (agent_id,)
    ).fetchone()
    conn.close()
    return row['letta_agent_id'] if row else None


def get_agent_registry_row(agent_id):
    """
    Return the full agent_registry row as a dict, or None.
    shared_peer_block_ids is parsed back into a list/dict if it was JSON.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM agent_registry WHERE agent_id=?", (agent_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    if d.get('shared_peer_block_ids'):
        try:
            d['shared_peer_block_ids'] = json.loads(d['shared_peer_block_ids'])
        except (ValueError, TypeError):
            pass  # leave as raw string if not JSON
    return d


# --- Phase 5: debate record helpers ---

def insert_debate_record(record_dict):
    """
    Insert a row into debate_records. record_dict keys map directly to
    column names. Any list/dict value for a column ending in _evidence is
    JSON-serialised before insert. If timestamp is omitted it is filled
    with datetime.utcnow().isoformat(). Returns cycle_id.
    """
    data = dict(record_dict)  # shallow copy so caller's dict is untouched
    data.setdefault('timestamp', datetime.utcnow().isoformat())

    for key, val in list(data.items()):
        if key.endswith('_evidence') and isinstance(val, (list, dict)):
            data[key] = json.dumps(val)
        elif key == 'hard_rule_overrides' and isinstance(val, (list, dict)):
            data[key] = json.dumps(val)
        elif key == 'freshness_retries' and isinstance(val, (list, dict)):
            data[key] = json.dumps(val)
        elif key == 'world_state' and isinstance(val, (list, dict)):
            # default=str mirrors council.update_world_state's serialization
            # so non-JSON-native values (e.g. datetimes) never raise here.
            data[key] = json.dumps(val, default=str)

    fields = ', '.join(data.keys())
    placeholders = ', '.join(['?' for _ in data])
    conn = get_conn()
    conn.execute(
        f'INSERT INTO debate_records ({fields}) VALUES ({placeholders})',
        list(data.values())
    )
    conn.commit()
    conn.close()
    return data.get('cycle_id')


def update_debate_outcomes(cycle_id, window, fills, pnl,
                            skew_delta=None, grid_alive=None,
                            unrealized_pnl=None):
    """
    Backfill outcome metrics on a debate_records row.
    window is one of '1h', '6h', '24h'. Sets fills_{window},
    pnl_{window}, outcome_{window}_backfilled=1. For the 6h window also
    optionally sets skew_delta_6h and grid_alive_6h. For the 6h and 24h
    windows also optionally sets unrealized_pnl_{window} (the windowed
    mark-to-market drift the observer backfill computes alongside realized;
    same live-only basis — 0.0 when no in-window live fills). There is no
    unrealized_pnl_1h column, so unrealized_pnl is ignored for the 1h window.
    """
    if window not in _VALID_WINDOWS:
        raise ValueError(f"window must be one of {_VALID_WINDOWS}, got {window!r}")

    sets = [
        f"fills_{window}=?",
        f"pnl_{window}=?",
        f"outcome_{window}_backfilled=1",
    ]
    vals = [fills, pnl]

    if window == '6h':
        if skew_delta is not None:
            sets.append("skew_delta_6h=?")
            vals.append(skew_delta)
        if grid_alive is not None:
            sets.append("grid_alive_6h=?")
            vals.append(int(bool(grid_alive)))

    if window in ('6h', '24h') and unrealized_pnl is not None:
        sets.append(f"unrealized_pnl_{window}=?")
        vals.append(unrealized_pnl)

    vals.append(cycle_id)
    conn = get_conn()
    conn.execute(
        f"UPDATE debate_records SET {', '.join(sets)} WHERE cycle_id=?",
        vals
    )
    conn.commit()
    conn.close()


def update_debate_applied(cycle_id, applied_grid_action, applied_spacing=None,
                          engine_clamped=0, clamp_reason=None):
    """Record what the ENGINE actually applied this cycle onto its
    debate_records row. Distinct from final_grid_action (the post-hard-rule
    council decision): this captures ENGINE-level divergence — the council-veto
    cross-check coercion, empty-book-guard skips, null-geometry refusals, and
    spacing clamps. Keyed by cycle_id (the row is already inserted by run_cycle
    before the engine applies). Non-fatal at the call site."""
    conn = get_conn()
    conn.execute(
        "UPDATE debate_records SET applied_grid_action=?, applied_spacing=?, "
        "engine_clamped=?, clamp_reason=? WHERE cycle_id=?",
        (applied_grid_action, applied_spacing,
         int(bool(engine_clamped)), clamp_reason, cycle_id),
    )
    conn.commit()
    conn.close()


def get_pending_outcome_backfills(window):
    """
    Return list of {cycle_id, timestamp} for debate_records whose
    outcome_{window}_backfilled=0 AND whose timestamp is at least N hours
    old (1, 6, or 24 depending on window). Ordered oldest-first.
    """
    if window not in _VALID_WINDOWS:
        raise ValueError(f"window must be one of {_VALID_WINDOWS}, got {window!r}")

    from datetime import timedelta
    hours_map = {'1h': 1, '6h': 6, '24h': 24}
    cutoff = (datetime.utcnow() - timedelta(hours=hours_map[window])).isoformat()

    conn = get_conn()
    rows = conn.execute(
        f'''SELECT cycle_id, timestamp FROM debate_records
            WHERE outcome_{window}_backfilled=0
              AND timestamp <= ?
            ORDER BY timestamp ASC''',
        (cutoff,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_score_pushes(window):
    """
    Return cycle_ids whose {window} outcome is backfilled but whose Langfuse
    score push has not been confirmed (outcome_{window}_scores_pushed=0).
    Oldest-first. Consumed by observer.push_pending_outcome_scores — the
    convergent retry sweep that replaced the fire-and-forget push (2026-06-11).
    Rows with NULL trace_id are excluded: they can never deliver (no trace to
    score), and because the caller processes only the head of the oldest-first
    queue each pass, an undeliverable NULL-trace row at the front would
    permanently clog the sweep for every row behind it.
    """
    if window not in _VALID_WINDOWS:
        raise ValueError(f"window must be one of {_VALID_WINDOWS}, got {window!r}")
    conn = get_conn()
    rows = conn.execute(
        f'''SELECT cycle_id FROM debate_records
            WHERE outcome_{window}_backfilled=1
              AND outcome_{window}_scores_pushed=0
              AND trace_id IS NOT NULL
            ORDER BY timestamp ASC'''
    ).fetchall()
    conn.close()
    return [r['cycle_id'] for r in rows]


def mark_outcome_scores_pushed(cycle_id, window):
    """Set the delivery receipt after push_trace_scores confirms every score
    in the window landed (HTTP 2xx). Never set on a failed/partial push —
    the sweep retries next pass."""
    if window not in _VALID_WINDOWS:
        raise ValueError(f"window must be one of {_VALID_WINDOWS}, got {window!r}")
    conn = get_conn()
    conn.execute(
        f"UPDATE debate_records SET outcome_{window}_scores_pushed=1 "
        f"WHERE cycle_id=?",
        (cycle_id,),
    )
    conn.commit()
    conn.close()


def get_recent_debate_records(limit=20):
    """Return the most recent N debate_records ordered by timestamp DESC."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM debate_records ORDER BY timestamp DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- magi_alerts ---

_ALERT_DEDUP_WINDOW_MINUTES = 60


def insert_eval_run(agent_id, suite_name, total_samples, passed_samples,
                    accuracy, gate_passed, gate_threshold,
                    cost_usd_estimate=None, raw_results_path=None,
                    git_commit_sha=None, notes=None):
    """Insert a single magi_eval_runs row. Returns the inserted row id."""
    conn = get_conn()
    cur = conn.execute(
        '''INSERT INTO magi_eval_runs
            (timestamp, agent_id, suite_name, total_samples, passed_samples,
             accuracy, gate_passed, gate_threshold, cost_usd_estimate,
             raw_results_path, git_commit_sha, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            datetime.utcnow().isoformat(),
            agent_id, suite_name,
            int(total_samples), int(passed_samples),
            float(accuracy),
            1 if gate_passed else 0,
            float(gate_threshold),
            None if cost_usd_estimate is None else float(cost_usd_estimate),
            raw_results_path, git_commit_sha, notes,
        )
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def get_recent_eval_runs(agent_id, limit=5):
    """Return up to `limit` most recent magi_eval_runs rows for one agent,
    newest first. Each row is a sqlite3.Row (dict-like)."""
    conn = get_conn()
    rows = conn.execute(
        '''SELECT id, timestamp, agent_id, suite_name, total_samples,
                  passed_samples, accuracy, gate_passed, gate_threshold,
                  cost_usd_estimate, raw_results_path, git_commit_sha, notes
           FROM magi_eval_runs
           WHERE agent_id=?
           ORDER BY timestamp DESC LIMIT ?''',
        (agent_id, limit),
    ).fetchall()
    conn.close()
    return rows


def insert_alert(severity, category, message, agent_id=None,
                 provider_category=None, provider_name=None, step_id=None):
    """
    Insert a row into magi_alerts.

    Dedup rules (both must miss for the row to be inserted):
      1. No unresolved row exists with the same
         (category, agent_id, provider_category) within the last 60 minutes.
      2. No row exists (resolved or not) with the same step_id.
         (step_id is a Letta-assigned unique identifier; if we've already
         alerted on a step, the background sweep should not re-alert.)

    Returns the inserted row id, or None if deduped.
    """
    conn = get_conn()
    if step_id:
        existing_step = conn.execute(
            "SELECT id FROM magi_alerts WHERE step_id=? LIMIT 1",
            (step_id,)
        ).fetchone()
        if existing_step:
            conn.close()
            return None
    cutoff = (
        datetime.utcnow() - timedelta(minutes=_ALERT_DEDUP_WINDOW_MINUTES)
    ).isoformat()
    existing = conn.execute(
        "SELECT id FROM magi_alerts "
        "WHERE category=? "
        "AND IFNULL(agent_id,'')=IFNULL(?,'') "
        "AND IFNULL(provider_category,'')=IFNULL(?,'') "
        "AND resolved=0 AND timestamp >= ? LIMIT 1",
        (category, agent_id, provider_category, cutoff)
    ).fetchone()
    if existing:
        conn.close()
        return None
    cur = conn.execute(
        "INSERT INTO magi_alerts (timestamp, severity, category, agent_id, "
        "provider_category, provider_name, step_id, message) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (datetime.utcnow().isoformat(), severity, category, agent_id,
         provider_category, provider_name, step_id,
         (message or '')[:500])
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    # Fire push notification for critical-severity alerts only. Wrapped in
    # try/except — the notification layer must never break alert capture.
    # send_ntfy() itself is also failure-tolerant; this is a belt-and-braces
    # guard for import-time errors.
    if severity == 'critical':
        try:
            from magi.notify import send_ntfy
            agent_part = f"{agent_id} " if agent_id else ""
            title = f"MAGI: {agent_part}{category}".strip()
            # Body intentionally OMITS the raw message text. ntfy.sh
            # topics are public, and critical-alert messages routinely
            # carry sensitive upstream payloads (402 responses include
            # remaining credit balances, API errors can include keys in
            # debug strings, etc.). The operator opens the dashboard for
            # the full detail — the push is just the "go look now" signal.
            body_parts = [f"[{severity.upper()}]"]
            if agent_id:
                body_parts.append(f"agent={agent_id}")
            if category:
                body_parts.append(f"cat={category}")
            body_parts.append("→ open dashboard")
            send_ntfy(
                title=title,
                body=' '.join(body_parts),
                severity=severity,
                agent_id=agent_id,
                category=category,
            )
        except Exception:
            pass
    return row_id


def get_open_alerts():
    """Return open (unresolved) alerts ordered critical → warn → info,
    then newest first within each severity."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, timestamp, severity, category, agent_id, "
        "provider_category, provider_name, step_id, message "
        "FROM magi_alerts WHERE resolved=0 "
        "ORDER BY CASE severity "
        "  WHEN 'critical' THEN 0 "
        "  WHEN 'warn' THEN 1 "
        "  WHEN 'info' THEN 2 "
        "  ELSE 3 END, "
        "timestamp DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_alert_timestamp():
    """Return the timestamp of the most recent alert row (any status),
    or None. Used by the background sweep to scope its lookback window."""
    conn = get_conn()
    row = conn.execute(
        "SELECT MAX(timestamp) AS ts FROM magi_alerts"
    ).fetchone()
    conn.close()
    return row['ts'] if row and row['ts'] else None


def mark_alert_resolved(alert_id):
    """Mark an alert resolved. Returns True if a row was updated."""
    conn = get_conn()
    cur = conn.execute(
        "UPDATE magi_alerts SET resolved=1, resolved_at=? "
        "WHERE id=? AND resolved=0",
        (datetime.utcnow().isoformat(), int(alert_id))
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


# Reality window for the PnL-column-based predicates (Melchior THESIS_HOLDS,
# Balthasar CLEAR/PROCEED): the 6h outcome — the primary, reliably-backfilled
# window the prior get_agent_accuracy also used. Casper and the counterfactual /
# NO_PROFITABLE_GRID sims use the forward_sim 72h horizon instead (their own basis).
_ACCURACY_WINDOW = '6h'
# Survival HALTs that can drive final_risk_action='HALT' independent of Balthasar's
# vote (so the HALT is the rule's, not his call).
_BALTHASAR_SURVIVAL_HALT_TAGS = {
    "[KILL_SWITCH]", "[DAILY_LOSS_LIMIT]", "[ALLOC_SKEW_CEILING]",
}

# The Stage-3 integration-test cycle artifact (id 270 / cyc_1780949300): a real
# convene written while wiring the orchestrator, NOT a live trading decision. It is
# hard-excluded from get_agent_recall by BOTH keys (id is DB-local; cycle_id is the
# stable identity). It is also off the current config_version, so the version filter
# would drop it anyway — the explicit exclusion is belt-and-suspenders.
_RECALL_EXCLUDE_ID = 270
_RECALL_EXCLUDE_CYCLE_ID = 'cyc_1780949300'


def _decision_bar_index(ts_keys, cycle_ts):
    """Index of the forward_sim decision bar = the latest 1h candle whose
    timestamp <= the cycle timestamp. ts_keys is the ascending list of candle
    timestamps normalised to 'YYYY-MM-DDTHH:MM:SS' (strips microseconds / tz
    offset so the lexicographic bisect is robust). Returns None if no bar
    precedes the cycle."""
    import bisect
    key = (cycle_ts or "")[:19]
    if not key:
        return None
    pos = bisect.bisect_right(ts_keys, key) - 1
    return pos if pos >= 0 else None


# --- per-row grading (single source of truth) ---
#
# Grading for each seat lives in exactly ONE place: these three helpers. Both the
# aggregate accuracy scorers (_score_casper/_melchior/_balthasar -> get_agent_accuracy)
# AND the per-seat recall Journal (get_agent_recall) call them, so the two readers can
# never drift. Each helper takes ONE debate_records Row (already SELECTed with the
# columns its seat needs, with the seat's r0 position aliased to 'position') plus the
# loaded 1h bars / bisect keys / bar count, and returns:
#
#     (grade_dict | None, excluded_reason | None)
#
# A gradeable row returns (grade_dict, None); an ungradeable one returns
# (None, reason) where reason is one of the seat's documented exclusion keys. The
# grade_dict fields:
#   bucket       — sub-category used by the aggregate scorers (Casper: 'regime';
#                  Melchior: the verdict; Balthasar: 'reality' | 'counterfactual').
#   correct      — bool, the seat's own correctness predicate.
#   basis        — 'reality' | 'sim' | 'counterfactual' (the grading_basis surfaced
#                  to the Journal; NEVER blend reality and counterfactual entries).
#   estimated    — True when the grade is a decision-time proxy / counterfactual the
#                  Journal must render as estimated_* (Melchior RECONFIGURE, applied
#                  Balthasar vetoes); False for outcome-realized grades.
#   label        — the call as it should appear in the Journal line header.
#   raw_outcome  — the ground-truth one-liner, ALWAYS present (the spine the Journal
#                  shows even when the grade is estimated).


def _grade_casper_row(r, bars, ts_keys, n):
    """Casper — regime-realized, PnL-independent, 72h forward horizon. Correct iff
    the row's regime call == the forward-realized label (forward_sim simulate->label).
    Ungradeable iff the 72h forward window is not yet fully covered by candles."""
    from grid.forward_sim import simulate, label, WINDOW_H
    i = _decision_bar_index(ts_keys, r['timestamp'])
    if i is None or i + WINDOW_H >= n:
        return None, 'not_matured_72h'
    realized_label, _dir = label(simulate(bars, i))
    return {
        'bucket': 'regime',
        'correct': (r['position'] == realized_label),
        'basis': 'reality',
        'estimated': False,
        'label': r['position'],
        'raw_outcome': (f"called {r['position']}; forward-realized {realized_label} "
                        f"over {WINDOW_H}h"),
    }, None


def _grade_melchior_row(r, bars, ts_keys, n):
    """Melchior — verdict-conditional, no shared predicate:
      THESIS_HOLDS        sim 72h  — the DEPLOYED config's sim alpha clears FEE_FLOOR
                                     AND no shown candidate beat it by > FEE_FLOOR
                                     (a switch is only justified when its edge exceeds
                                     the round-trip cost of getting there).
      NO_PROFITABLE_GRID  sim 72h  — simulate alpha_pct <= FEE_FLOOR (no fee-clearing
                                     grid existed). Graded, not raw-only: the raw
                                     "0 fills" alone re-teaches over-trading.
      RECONFIGURE         sim/proxy— the best scored variant Melchior was shown beat
                                     the current config AND pnl_6h>=0 (decision-time
                                     proxy; estimated_* in the Journal).
    Non-verdict positions (Letta-era MAINTAIN/RECENTRE) are ungradeable."""
    from grid.forward_sim import simulate, FEE_FLOOR, WINDOW_H
    verdict = r['position']
    if verdict == 'THESIS_HOLDS':
        # Sim-graded as of 2026-06-12. The prior reality predicate (fills_6h>0
        # AND pnl_6h>=0 AND pnl+unreal>=0) graded the market, not the verdict:
        # at 1.5-2.5% spacing most 6h windows fill nothing, so every
        # quiet-but-correct hold scored wrong (0/16 on the paper run) and the
        # Journal fed Melchior false always-failing feedback. THESIS_HOLDS and
        # NO_PROFITABLE_GRID now partition on the same exogenous FEE_FLOOR over
        # the shared 72h horizon.
        # TRADEOFF (accepted): truth-standard is the forward sim, not real
        # fills — same basis as Casper / NO_PROFITABLE_GRID / Balthasar's
        # counterfactual. Escalation fix if sim and reality diverge: once the
        # wide grid has enough real round trips, compare matured sim grades
        # against realized round-trip PnL over the same windows.
        i = _decision_bar_index(ts_keys, r['timestamp'])
        if i is None or i + WINDOW_H >= n:
            return None, 'not_matured_72h'
        raw = r['world_state']
        if not raw:
            return None, 'missing_world_state'
        try:
            ws = json.loads(raw)
        except Exception:
            return None, 'missing_world_state'
        spacing = ws.get('current_spacing_pct')
        levels = ws.get('current_levels')
        if not spacing or not levels:
            return None, 'missing_world_state'
        # world_state levels are TOTAL (spacing_evaluator: n_pairs = levels//2);
        # forward_sim n_levels is PER SIDE.
        per_side = max(1, int(levels) // 2)
        deployed = simulate(bars, i, spacing_pct=float(spacing), n_levels=per_side)
        thesis_ok = deployed['alpha_pct'] > FEE_FLOOR
        top = ws.get('scored_variants_top_10') or []
        rank1 = top[0] if top else None
        no_better = True
        rival_note = "no scored candidates shown"
        if rank1 and rank1.get('spacing_pct') and rank1.get('levels'):
            r1_sp, r1_lv = float(rank1['spacing_pct']), int(rank1['levels'])
            if (r1_sp, r1_lv) == (float(spacing), int(levels)):
                rival_note = "rank-1 candidate = deployed config"
            else:
                rival = simulate(bars, i, spacing_pct=r1_sp,
                                 n_levels=max(1, r1_lv // 2))
                edge = rival['alpha_pct'] - deployed['alpha_pct']
                no_better = edge <= FEE_FLOOR
                rival_note = (f"best shown variant ({r1_lv}L/{r1_sp * 100:.2f}%) "
                              f"sim alpha {rival['alpha_pct']:+.2f}%, edge "
                              f"{edge:+.2f}% vs {FEE_FLOOR:.2f}% switch floor")
        return {
            'bucket': 'THESIS_HOLDS',
            'correct': (thesis_ok and no_better),
            'basis': 'sim', 'estimated': False, 'label': 'THESIS_HOLDS',
            'raw_outcome': (f"held grid: deployed sim alpha "
                            f"{deployed['alpha_pct']:+.2f}% vs fee floor "
                            f"{FEE_FLOOR:.2f}% over {WINDOW_H}h; {rival_note}"),
        }, None
    if verdict == 'NO_PROFITABLE_GRID':
        i = _decision_bar_index(ts_keys, r['timestamp'])
        if i is None or i + WINDOW_H >= n:
            return None, 'not_matured_72h'
        d = simulate(bars, i)
        return {
            'bucket': 'NO_PROFITABLE_GRID',
            'correct': (d['alpha_pct'] <= FEE_FLOOR),
            'basis': 'sim', 'estimated': False, 'label': 'NO_PROFITABLE_GRID',
            'raw_outcome': (f"stood down; sim grid alpha {d['alpha_pct']:+.2f}% vs "
                            f"fee floor {FEE_FLOOR:.2f}% over {WINDOW_H}h"),
        }, None
    if verdict == 'RECONFIGURE':
        # PROXY: grades the DECISION on decision-time info, NOT a true held-the-old-
        # config counterfactual (no such realized series exists) -> estimated_*.
        raw = r['world_state']
        if not raw:
            return None, 'missing_world_state'
        try:
            ws = json.loads(raw)
        except Exception:
            return None, 'missing_world_state'
        top = ws.get('scored_variants_top_10') or []
        cur = ws.get('current_config_expected_daily_pnl_pct')
        chosen = top[0].get('expected_daily_pnl_pct') if top else None
        pnl = r['pnl_6h']
        if chosen is None or cur is None or pnl is None:
            return None, 'missing_world_state'
        return {
            'bucket': 'RECONFIGURE',
            'correct': (chosen > cur and pnl >= 0),
            'basis': 'sim', 'estimated': True, 'label': 'RECONFIGURE',
            'raw_outcome': (f"reconfigured; chosen variant {chosen:+.3f} vs current "
                            f"{cur:+.3f} daily%, realized {pnl:+.4f} (6h)"),
        }, None
    return None, 'non_verdict_position'   # predates the verdict model


def _grade_balthasar_row(r, bars, ts_keys, n):
    """Balthasar — total-PnL + applied-flag, reality/counterfactual kept SEPARATE:
      Applied CLEAR/PROCEED  reality        — correct iff (pnl_6h + unreal) >= 0.
      Applied veto           counterfactual — correct iff the unpaused grid would have
        (sim 72h)                             bled (simulate grid_pnl < 0); rendered
                                              estimated_* in the Journal.
      Overridden by hard rules               — ungradeable (not his call).
    Applied-vs-overridden recovered from balthasar_r0_position + final_risk_action +
    geometry_veto + the hard_rule_overrides tags."""
    from grid.forward_sim import simulate, WINDOW_H
    pos = r['position']                 # risk_action
    gveto = r['geometry_veto']
    final_grid = r['final_grid_action']
    final_risk = r['final_risk_action']
    try:
        overrides = json.loads(r['hard_rule_overrides']) if r['hard_rule_overrides'] else []
    except Exception:
        overrides = []
    if not isinstance(overrides, list):
        overrides = []

    # 1. Overridden / not-driving -> ungradeable.
    overridden = (
        '[AGENT_DEGRADED:balthasar]' in overrides
        or '[COUNCIL_COLLAPSED]' in overrides
        or (final_risk == 'HALT' and pos != 'HALT'
            and any(t in overrides for t in _BALTHASAR_SURVIVAL_HALT_TAGS))
    )
    if overridden:
        return None, 'overridden_hard_rule'

    # 2. Was his veto applied? (Stage-4 item 2a: the structural geometry veto moved
    # from hard-rule 0d into the arbiter's synthesis vote — APPLIED iff he voted
    # HOLD_GEOMETRY/RISK_BLOCK and the grid was held to MAINTAIN this cycle.)
    veto_applied = (
        (pos in ('PAUSE_LONGS', 'PAUSE_SHORTS', 'HALT') and final_risk == pos)
        or (gveto in ('HOLD_GEOMETRY', 'RISK_BLOCK') and final_grid == 'MAINTAIN')
    )
    if veto_applied:
        # COUNTERFACTUAL: the actual window is paused (~0 PnL), so grade against the
        # simulated unpaused grid. A negative counterfactual PnL means the brake
        # earned its keep; a positive one means the veto bailed before a recovery.
        i = _decision_bar_index(ts_keys, r['timestamp'])
        if i is None or i + WINDOW_H >= n:
            return None, 'not_matured_72h'
        d = simulate(bars, i)
        veto_kind = pos if pos in ('PAUSE_LONGS', 'PAUSE_SHORTS', 'HALT') else gveto
        return {
            'bucket': 'counterfactual',
            'correct': (d['grid_pnl'] < 0),
            'basis': 'counterfactual', 'estimated': True,
            'label': f"VETO({veto_kind})",
            'raw_outcome': (f"blocked the grid; sim unpaused-grid PnL "
                            f"{d['grid_pnl']:+.3f} over {WINDOW_H}h"),
        }, None
    # 3. REALITY: CLEAR/PROCEED — grid ran, score actual total PnL.
    pnl = r['pnl_6h']
    if pnl is None:
        return None, 'missing_outcome'
    unreal = r['unrealized_pnl_6h'] or 0.0              # COALESCE NULL -> 0.0
    return {
        'bucket': 'reality',
        'correct': ((pnl + unreal) >= 0),
        'basis': 'reality', 'estimated': False, 'label': pos,
        'raw_outcome': (f"applied {pos}: total PnL realized {pnl:+.4f} + "
                        f"unrealized {unreal:+.4f} (6h)"),
    }, None


def _grade_action_row(r, bars, ts_keys, n):
    """Blind-review SYMMETRIC seat grader — one anchored predicate for ALL THREE
    co-equal seats (governing principle P1), grading each seat's RAW proposed action
    (r['action'] from {seat}_r0_action) against the shared 72h forward sim. This is
    NOT a per-role special-case ladder (the old per-seat regime/verdict/risk graders);
    it collapses them onto TWO axes, each matched to what the action actually controls:

      grid-run/stop — graded on GRID ECONOMICS (grid-vs-hold alpha):
        MAINTAIN / RECONFIGURE  correct iff the forward grid harvested above costs
                                -> sim alpha_pct >  FEE_FLOOR
        HALT                    correct iff running it would have bled vs hold
                                -> sim alpha_pct < -FEE_FLOOR
      exposure-direction — graded on realized PRICE DIRECTION (not grid alpha; a
        de-risk through a rally must score wrong, which the alpha axis would miss):
        STAND_ASIDE / PAUSE_LONGS  correct iff price fell -> forward drift < 0
        PAUSE_SHORTS               correct iff price rose -> forward drift > 0

    Anchored ONLY to FEE_FLOOR (exogenous: 2*MAKER_FEE, the round-trip break-even) and
    the forward price direction — no thresholds fit to data — and it reuses the exact
    forward_sim truth-standard every other grader already uses. OBSERVABILITY ONLY:
    the grade is mirrored to Langfuse and never feeds back into a council decision or
    vote weight (the tally stays flat). Ungradeable iff the 72h window is not yet
    fully covered by candles, or no action was authored (non-responder / arbiter-era
    row -> NULL, neither right nor wrong)."""
    from grid.forward_sim import simulate, FEE_FLOOR, WINDOW_H
    action = r.get('action')
    if not action:
        return None, 'no_action'
    i = _decision_bar_index(ts_keys, r['timestamp'])
    if i is None or i + WINDOW_H >= n:
        return None, 'not_matured_72h'
    d = simulate(bars, i)
    a, drift = d['alpha_pct'], d['drift_pct']
    if action in ('MAINTAIN', 'RECONFIGURE'):
        correct = a > FEE_FLOOR
        note = (f"deployed ({action}); forward grid alpha {a:+.2f}% vs fee floor "
                f"{FEE_FLOOR:.2f}% over {WINDOW_H}h")
    elif action == 'HALT':
        # grid-STOP decision: correct iff running the grid would have bled vs hold.
        correct = a < -FEE_FLOOR
        note = (f"halted; forward grid alpha {a:+.2f}% vs -{FEE_FLOOR:.2f}% "
                f"bleed floor over {WINDOW_H}h")
    elif action in ('STAND_ASIDE', 'PAUSE_LONGS'):
        # withhold / shed LONG exposure: graded on realized DIRECTION, not grid
        # alpha — these are right iff price fell (a de-risk through a rally is wrong,
        # which the grid-alpha axis would mis-score). Matches the stance grader.
        correct = drift < 0
        verb = 'stood aside' if action == 'STAND_ASIDE' else 'paused longs'
        note = f"{verb} ({action}); forward drift {drift:+.2f}% over {WINDOW_H}h"
    elif action == 'PAUSE_SHORTS':
        # withhold SELL side: right iff price rose.
        correct = drift > 0
        note = f"paused shorts; forward drift {drift:+.2f}% over {WINDOW_H}h"
    else:
        return None, 'unknown_action'
    return {
        'bucket': 'action', 'correct': bool(correct), 'basis': 'sim',
        'estimated': False, 'label': action, 'raw_outcome': note,
    }, None


def _score_casper(conn, bars, ts_keys, cutoff):
    """Casper — regime-realized, PnL-independent, 72h horizon. A call is correct
    iff casper_r0_position == the forward-realized regime label (forward_sim:
    simulate->label over WINDOW_H=72h, FEE_FLOOR=2*MAKER_FEE), computed from 1h
    candles and INDEPENDENT of the pnl_* columns. UNCERTAIN is matched-to-
    ambiguous: correct iff the realized label is also UNCERTAIN (no abstention
    exclusion). A row whose 72h forward window is not yet fully covered by candles
    is NOT scored (excluded as not-matured), never counted wrong. Grading delegates
    to _grade_casper_row (the single source of truth shared with get_agent_recall)."""
    n = len(bars)
    rows = conn.execute(
        '''SELECT casper_r0_position AS position, timestamp
           FROM debate_records
           WHERE timestamp >= ? AND casper_r0_position IS NOT NULL''',
        (cutoff,)
    ).fetchall()
    scored = correct = not_matured = 0
    for r in rows:
        grade, _reason = _grade_casper_row(r, bars, ts_keys, n)
        if grade is None:                       # casper's sole exclusion: not matured
            not_matured += 1
            continue
        scored += 1
        if grade['correct']:
            correct += 1
    acc = round(correct / scored * 100.0, 2) if scored else None
    return {
        'eligible_calls': len(rows),
        'role_basis': 'regime_realized_72h',
        'scored': scored, 'correct': correct, 'accuracy_pct': acc,
        'excluded': not_matured,
        'excluded_reasons': {'not_matured_72h': not_matured},
    }


def _score_melchior(conn, bars, ts_keys, cutoff):
    """Melchior — verdict-conditional. Per-verdict, no shared predicate:
      THESIS_HOLDS  (sim 72h)        correct iff the DEPLOYED config's sim alpha
                                     > FEE_FLOOR AND no shown candidate beat it
                                     by > FEE_FLOOR (switch-cost materiality bar).
      NO_PROFITABLE_GRID (sim 72h)   correct iff simulate alpha_pct <= FEE_FLOOR
                                     (no fee-clearing grid; 2*MAKER_FEE floor).
      RECONFIGURE   (decision-time PROXY) correct iff the best-ranked scored
                                     variant Melchior was shown beat the current
                                     config on expected daily PnL AND pnl_6h>=0.
    Rows whose position is not one of the three verdicts (e.g. Letta-era
    MAINTAIN/RECENTRE action vocabulary) are excluded as non-verdict. Grading
    delegates to _grade_melchior_row (the single source of truth shared with
    get_agent_recall)."""
    n = len(bars)
    rows = conn.execute(
        '''SELECT melchior_r0_position AS position, timestamp,
                  fills_6h, pnl_6h, unrealized_pnl_6h, world_state
           FROM debate_records
           WHERE timestamp >= ? AND melchior_r0_position IS NOT NULL''',
        (cutoff,)
    ).fetchall()
    scored = correct = 0
    by_verdict = {v: {'scored': 0, 'correct': 0}
                  for v in ('THESIS_HOLDS', 'RECONFIGURE', 'NO_PROFITABLE_GRID')}
    excl = {'non_verdict_position': 0, 'not_matured_72h': 0,
            'missing_outcome': 0, 'missing_world_state': 0}

    for r in rows:
        grade, reason = _grade_melchior_row(r, bars, ts_keys, n)
        if grade is None:
            excl[reason] = excl.get(reason, 0) + 1
            continue
        scored += 1
        by_verdict[grade['bucket']]['scored'] += 1
        if grade['correct']:
            correct += 1
            by_verdict[grade['bucket']]['correct'] += 1

    acc = round(correct / scored * 100.0, 2) if scored else None
    return {
        'eligible_calls': len(rows),
        'role_basis': 'verdict_conditional',
        'scored': scored, 'correct': correct, 'accuracy_pct': acc,
        'excluded': sum(excl.values()),
        'excluded_reasons': excl,
        'by_verdict': by_verdict,
    }


def _score_balthasar(conn, bars, ts_keys, cutoff):
    """Balthasar — total-PnL + applied-flag, with the reality/counterfactual split
    kept SEPARATE (never summed into one accuracy number):

      Applied CLEAR/PROCEED  (REALITY)        correct iff (pnl_6h +
                                              unrealized_pnl_6h) >= 0.
      Applied veto PAUSE/HOLD/RISK_BLOCK (COUNTERFACTUAL, sim 72h) correct iff the
                                              unpaused grid would have bled
                                              (grid_equity_end - grid_equity_start
                                              < 0); a positive counterfactual means
                                              the veto was wrong (bailed before a
                                              recovery).
      Overridden by hard rules               EXCLUDED / N-A (not his call).

    Applied-vs-overridden is recovered from balthasar_r0_position + final_risk_action
    + geometry_veto + the hard_rule_overrides tags. Grading delegates to
    _grade_balthasar_row (the single source of truth shared with get_agent_recall)."""
    n = len(bars)
    rows = conn.execute(
        '''SELECT balthasar_r0_position AS position, timestamp,
                  fills_6h, pnl_6h, unrealized_pnl_6h,
                  geometry_veto, final_grid_action, final_risk_action,
                  hard_rule_overrides
           FROM debate_records
           WHERE timestamp >= ? AND balthasar_r0_position IS NOT NULL''',
        (cutoff,)
    ).fetchall()

    reality = {'scored': 0, 'correct': 0}
    counterfactual = {'scored': 0, 'correct': 0}
    excl = {'overridden_hard_rule': 0, 'not_matured_72h': 0, 'missing_outcome': 0}

    for r in rows:
        grade, reason = _grade_balthasar_row(r, bars, ts_keys, n)
        if grade is None:
            excl[reason] = excl.get(reason, 0) + 1
            continue
        bucket = reality if grade['bucket'] == 'reality' else counterfactual
        bucket['scored'] += 1
        if grade['correct']:
            bucket['correct'] += 1

    r_acc = (round(reality['correct'] / reality['scored'] * 100.0, 2)
             if reality['scored'] else None)
    c_acc = (round(counterfactual['correct'] / counterfactual['scored'] * 100.0, 2)
             if counterfactual['scored'] else None)
    return {
        'eligible_calls': len(rows),
        'role_basis': 'total_pnl_applied_flag',
        'reality_graded': {**reality, 'accuracy_pct': r_acc},
        'counterfactual_graded': {**counterfactual, 'accuracy_pct': c_acc},
        'excluded': sum(excl.values()),
        'excluded_reasons': excl,
    }


def get_agent_accuracy(agent_id, days=7):
    """
    Per-role forward-outcome accuracy for an agent's r0 calls over the last
    `days` days. Each seat is scored on its OWN question — there is no shared
    fills>0 AND pnl>=0 predicate (that was the database.py:1635 bug):

      casper    — regime-realized (forward_sim 72h label vs casper_r0_position),
                  PnL-independent. (see _score_casper)
      melchior  — verdict-conditional (THESIS_HOLDS sim 72h / NO_PROFITABLE_GRID
                  sim 72h / RECONFIGURE decision-time proxy). (see _score_melchior)
      balthasar — total-PnL + applied-flag, with reality-graded (CLEAR/PROCEED)
                  and counterfactual-graded (applied veto, sim) kept SEPARATE.
                  (see _score_balthasar)

    Return shape (grew from the old {total_calls, positive_outcomes,
    accuracy_pct}; the back-compat scalar keys are preserved on every agent):

      common:  agent_id, days, role_basis, eligible_calls, scored, excluded,
               excluded_reasons, total_calls (= eligible_calls back-compat).
      casper / melchior:  positive_outcomes, accuracy_pct  (correct / scored).
                          melchior adds by_verdict.
      balthasar:          reality_graded {scored, correct, accuracy_pct} and
                          counterfactual_graded {scored, correct, accuracy_pct}
                          carried SEPARATELY. The back-compat positive_outcomes /
                          accuracy_pct are the REALITY figures ONLY — the
                          counterfactual veto results are NEVER summed into them;
                          read counterfactual_graded for those. accuracy_basis_note
                          documents this in the payload.

    accuracy_pct is None when nothing was scored (e.g. no matured rows). Live
    consumers: dashboard.py (_fetch_council_data, paper-run-scoped fractional
    `days`, and /api/council/accuracy). `days` may be a float — the cutoff is
    utcnow() - timedelta(days=days).
    """
    if agent_id not in _VALID_AGENT_IDS:
        raise ValueError(f"unknown agent_id: {agent_id!r}")

    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    from grid.forward_sim import load_1h
    conn = get_conn()
    try:
        bars = load_1h(conn)
        ts_keys = [b[0][:19] for b in bars]     # normalised ascending bisect keys
        if agent_id == 'casper':
            role = _score_casper(conn, bars, ts_keys, cutoff)
        elif agent_id == 'melchior':
            role = _score_melchior(conn, bars, ts_keys, cutoff)
        else:  # balthasar
            role = _score_balthasar(conn, bars, ts_keys, cutoff)
    finally:
        conn.close()

    out = {
        'agent_id': agent_id,
        'days': days,
        'role_basis': role['role_basis'],
        'eligible_calls': role['eligible_calls'],
        'total_calls': role['eligible_calls'],      # back-compat alias
        'excluded': role['excluded'],
        'excluded_reasons': role['excluded_reasons'],
    }
    if agent_id == 'balthasar':
        out['reality_graded'] = role['reality_graded']
        out['counterfactual_graded'] = role['counterfactual_graded']
        # Back-compat scalars: REALITY basis ONLY (never blended w/ counterfactual).
        out['scored'] = role['reality_graded']['scored']
        out['positive_outcomes'] = role['reality_graded']['correct']
        out['accuracy_pct'] = role['reality_graded']['accuracy_pct']
        out['accuracy_basis_note'] = (
            "accuracy_pct / positive_outcomes are REALITY-graded only "
            "(applied CLEAR/PROCEED). Simulation-graded applied-veto calls are in "
            "counterfactual_graded and are deliberately NOT summed into them."
        )
    else:
        out['scored'] = role['scored']
        out['positive_outcomes'] = role['correct']
        out['accuracy_pct'] = role['accuracy_pct']
        if agent_id == 'melchior':
            out['by_verdict'] = role['by_verdict']
    return out


# --- per-agent recall: the "Journal" (deterministic, prompt-injected) ---

# SELECT column lists per seat — exactly the columns that seat's grade helper reads,
# with its r0 position aliased to 'position'. id + cycle_id + config_version are
# common (filtering / exclusion); timestamp drives ordering and the date header.
_RECALL_COLS = {
    'casper': "casper_r0_position AS position, timestamp",
    'melchior': ("melchior_r0_position AS position, timestamp, "
                 "fills_6h, pnl_6h, unrealized_pnl_6h, world_state"),
    'balthasar': ("balthasar_r0_position AS position, timestamp, "
                  "fills_6h, pnl_6h, unrealized_pnl_6h, geometry_veto, "
                  "final_grid_action, final_risk_action, hard_rule_overrides"),
}
_RECALL_POS_COL = {
    'casper': 'casper_r0_position',
    'melchior': 'melchior_r0_position',
    'balthasar': 'balthasar_r0_position',
}
_RECALL_GRADER = {
    'casper': _grade_casper_row,
    'melchior': _grade_melchior_row,
    'balthasar': _grade_balthasar_row,
}
_RECALL_HEADER = "=== YOUR RECALL — your own past calls, scored by outcome (private) ==="
_RECALL_EMPTY_SENTINEL = _RECALL_HEADER + "\n(no validated history yet)"


def _render_grade_word(grade):
    """Map a grade dict to the Journal's grade vocabulary. estimated grades (the
    decision-time proxy / counterfactual ones) are rendered estimated_*; outcome-
    realized grades are plain correct/incorrect."""
    if grade['estimated']:
        return 'estimated_correct' if grade['correct'] else 'estimated_incorrect'
    return 'correct' if grade['correct'] else 'incorrect'


def _render_recall_block(entries):
    """Render the per-seat recall entries into the literal, deterministically-ordered
    block injected into the seat's prompt. No now()-timestamps; chronological order.
    Empty -> the explicit sentinel (never an error). The grading_basis is surfaced as
    a parenthetical when it is not plain reality, so 'estimated' vs 'sim' vs
    'counterfactual' is legible on the line itself."""
    if not entries:
        return _RECALL_EMPTY_SENTINEL
    lines = [_RECALL_HEADER]
    for e in entries:
        basis_suffix = '' if e['grading_basis'] == 'reality' else f" ({e['grading_basis']})"
        lines.append(
            f"[{e['date']} {e['label']}] {e['raw_outcome']} | {e['grade']}{basis_suffix}"
        )
    return "\n".join(lines)


def get_agent_recall(agent_id, config_version, as_of=None):
    """Per-agent recall — the deterministic "Journal". A pure SQLite read (NO model
    call, NO vendor cost): the same inputs produce byte-identical output. Each seat
    recalls ONLY its own past calls, scored by ITS OWN per-role metric — there is no
    cross-agent history. The live consumer is council_v2.run_council, which injects
    each seat's block as prompt context.

    LAYERING: config_version is a PARAMETER supplied by the caller, not computed here.
    The decision layer (council_v2) owns the fingerprint and passes the current
    version down; this data-layer function is a pure "filter by the version I'm given"
    read and does NOT import council_v2 or orchestrator. If the caller passes None
    (could not establish the boundary), recall is EMPTY — a fail-safe miss, never a
    cross-config injection.

    Scoping filters, applied IN ORDER:
      (a) config boundary — include only rows whose config_version EQUALS the
          `config_version` argument. A row written under different personas/models/
          rules/disclosure is a different regime and must not be recalled as if it
          taught the current one. config_version is None -> recall is EMPTY.
      (b) scored-only — a row is included ONLY if THAT seat's grade helper produces a
          grade (delegated to _grade_*_row; ungraded rows are skipped, never
          reimplemented here).
      (c) bounds — the most-recent RECALL_MAX_ITEMS graded rows within
          RECALL_LOOKBACK_DAYS of `as_of`.
      (d) hard-exclude the Stage-3 integration-test artifact (id 270 /
          cyc_1780949300) by both keys.

    as_of (str ISO or datetime) anchors the lookback window and the recency cut;
    None -> MAX(timestamp) in debate_records (DB-derived, so still deterministic —
    never utcnow()).

    Returns a dict:
      {agent_id, as_of, config_version, entries, block}
    where each entry carries THREE fields plus presentation:
      raw_outcome   — ALWAYS present: the ground-truth one-liner (the spine).
      grade         — correct | incorrect | estimated_correct | estimated_incorrect.
      grading_basis — reality | sim | counterfactual (reality and counterfactual are
                      kept SEPARATE, never blended).
    `block` is the rendered, prompt-ready text (the empty sentinel when no entry
    survives the filters)."""
    if agent_id not in _VALID_AGENT_IDS:
        raise ValueError(f"unknown agent_id: {agent_id!r}")

    cfg_version = config_version     # (a) boundary supplied by caller; None -> empty

    conn = get_conn()
    try:
        from grid.forward_sim import load_1h
        bars = load_1h(conn)
        ts_keys = [b[0][:19] for b in bars]
        n = len(bars)

        if as_of is None:
            row = conn.execute(
                "SELECT MAX(timestamp) AS ts FROM debate_records").fetchone()
            as_of = row['ts'] if row and row['ts'] else None

        entries = []
        if cfg_version is not None and as_of is not None:
            as_of_s = as_of if isinstance(as_of, str) else as_of.isoformat()
            try:
                lower = (datetime.fromisoformat(as_of_s)
                         - timedelta(days=RECALL_LOOKBACK_DAYS)).isoformat()
            except ValueError:
                lower = None
            if lower is not None:
                pos_col = _RECALL_POS_COL[agent_id]
                sql = (
                    f"SELECT {_RECALL_COLS[agent_id]} "
                    f"FROM debate_records "
                    f"WHERE config_version = ? "
                    f"  AND timestamp >= ? AND timestamp <= ? "
                    f"  AND {pos_col} IS NOT NULL "
                    f"  AND id != ? "
                    f"  AND (cycle_id IS NULL OR cycle_id != ?) "
                    f"ORDER BY timestamp DESC, id DESC"
                )
                rows = conn.execute(
                    sql,
                    (cfg_version, lower, as_of_s,
                     _RECALL_EXCLUDE_ID, _RECALL_EXCLUDE_CYCLE_ID),
                ).fetchall()
                grader = _RECALL_GRADER[agent_id]
                for r in rows:                  # most-recent first
                    grade, _reason = grader(r, bars, ts_keys, n)
                    if grade is None:           # (b) scored-only: skip ungraded
                        continue
                    entries.append({
                        'date': (r['timestamp'] or '')[:10],
                        'label': grade['label'],
                        'raw_outcome': grade['raw_outcome'],
                        'grade': _render_grade_word(grade),
                        'grading_basis': grade['basis'],
                    })
                    if len(entries) >= RECALL_MAX_ITEMS:   # (c) bound count
                        break
                entries.reverse()               # inject oldest -> newest
    finally:
        conn.close()

    return {
        'agent_id': agent_id,
        'as_of': (as_of if isinstance(as_of, str)
                  else (as_of.isoformat() if as_of else None)),
        'config_version': cfg_version,
        'entries': entries,
        'block': _render_recall_block(entries),
    }


_LEDGER_HEADER = ("=== COUNCIL LEDGER — the council's own recent decisions and how "
                  "they turned out (shared, authorship-free) ===")
_LEDGER_EMPTY_SENTINEL = _LEDGER_HEADER + "\n(no prior council decisions yet)"


def _render_ledger_block(entries):
    """Render the council-ledger entries into a deterministic, prompt-ready block
    injected IDENTICALLY to all three seats. Authorship-free by construction — it
    carries the decision + the authorship-free vote multiset + the matured outcome,
    never which seat proposed what. Empty -> the explicit sentinel."""
    if not entries:
        return _LEDGER_EMPTY_SENTINEL
    lines = [_LEDGER_HEADER]
    for e in entries:
        outcome = e['outcome'] or "outcome pending"
        lines.append(
            f"[{e['date']}] decision={e['decision']} ({e['consensus']}) | "
            f"votes [{e['vote_multiset']}] | {outcome}"
        )
    return "\n".join(lines)


def get_council_ledger(config_version, as_of=None):
    """The COUNCIL'S OWN memory — a pure SQLite read (NO model call), replay-safe:
    the same inputs produce byte-identical output. Unlike the per-seat Journal
    (get_agent_recall), this is ONE shared, authorship-free block injected IDENTICALLY
    to all three seats in Phase 1 (the blind-review council is co-equal — no seat gets
    a privileged view of the past). It stores only what the council needs to recall
    its OWN past: the decision, the authorship-free vote multiset, the consensus class,
    and the matured 24h outcome. Nothing here is added for auditing or monitoring.

    Source: the additive `council_json` column (written by the blind-review council)
    joined with the existing matured-outcome columns (fills_24h / pnl_24h, set later
    by the backfill path). config_version is a PARAMETER supplied by the caller (the
    decision layer owns the fingerprint); None -> EMPTY ledger (fail-safe miss, never
    cross-config recall). Same boundary/lookback/count discipline as get_agent_recall:
      (a) config boundary — only rows whose config_version EQUALS the argument.
      (b) council-only — only rows that carry a council_json (pre-redesign rows have
          NULL council_json and are skipped — no arbiter-relay history leaks in).
      (c) bounds — most-recent RECALL_MAX_ITEMS rows within RECALL_LOOKBACK_DAYS.

    Returns {as_of, config_version, entries, block}; `block` is the rendered text
    (empty sentinel when nothing survives the filters)."""
    cfg_version = config_version
    conn = get_conn()
    try:
        if as_of is None:
            row = conn.execute(
                "SELECT MAX(timestamp) AS ts FROM debate_records").fetchone()
            as_of = row['ts'] if row and row['ts'] else None

        entries = []
        if cfg_version is not None and as_of is not None:
            as_of_s = as_of if isinstance(as_of, str) else as_of.isoformat()
            try:
                lower = (datetime.fromisoformat(as_of_s)
                         - timedelta(days=RECALL_LOOKBACK_DAYS)).isoformat()
            except ValueError:
                lower = None
            if lower is not None:
                rows = conn.execute(
                    "SELECT timestamp, council_json, fills_24h, pnl_24h, "
                    "       outcome_24h_backfilled "
                    "FROM debate_records "
                    "WHERE config_version = ? "
                    "  AND timestamp >= ? AND timestamp <= ? "
                    "  AND council_json IS NOT NULL "
                    "  AND id != ? "
                    "  AND (cycle_id IS NULL OR cycle_id != ?) "
                    "ORDER BY timestamp DESC, id DESC",
                    (cfg_version, lower, as_of_s,
                     _RECALL_EXCLUDE_ID, _RECALL_EXCLUDE_CYCLE_ID),
                ).fetchall()
                for r in rows:                      # most-recent first
                    try:
                        cj = json.loads(r['council_json']) if r['council_json'] else None
                    except (ValueError, TypeError):
                        cj = None
                    if not isinstance(cj, dict):     # (b) council-only: skip unparseable
                        continue
                    outcome = None
                    if r['outcome_24h_backfilled']:
                        fills = r['fills_24h']
                        pnl = r['pnl_24h']
                        pnl_s = f"${pnl:+.2f}" if isinstance(pnl, (int, float)) else "n/a"
                        outcome = f"24h: {fills if fills is not None else 'n/a'} fills, {pnl_s}"
                    entries.append({
                        'date': (r['timestamp'] or '')[:10],
                        'decision': cj.get('decision'),
                        'vote_multiset': cj.get('vote_multiset', ''),
                        'consensus': cj.get('consensus'),
                        'outcome': outcome,
                    })
                    if len(entries) >= RECALL_MAX_ITEMS:    # (c) bound count
                        break
                entries.reverse()                   # inject oldest -> newest
    finally:
        conn.close()

    return {
        'as_of': (as_of if isinstance(as_of, str)
                  else (as_of.isoformat() if as_of else None)),
        'config_version': cfg_version,
        'entries': entries,
        'block': _render_ledger_block(entries),
    }


def get_capitulation_rate(agent_id, days=7):
    """
    Return {total_revisions, invalid_revisions, capitulation_pct} for the
    agent's r1 revisions over the last `days` days. A 'revision' is any
    row where the agent did NOT hold (revision_valid is non-null);
    'invalid' means revision_valid=0.
    """
    if agent_id not in _VALID_AGENT_IDS:
        raise ValueError(f"unknown agent_id: {agent_id!r}")

    from datetime import timedelta
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    rev_col = f"{agent_id}_revision_valid"

    conn = get_conn()
    rows = conn.execute(
        f'''SELECT {rev_col} AS rev FROM debate_records
            WHERE timestamp >= ?
              AND {rev_col} IS NOT NULL''',
        (cutoff,)
    ).fetchall()
    conn.close()

    total = len(rows)
    invalid = sum(1 for r in rows if r['rev'] == 0)
    pct = (invalid / total * 100.0) if total > 0 else 0.0
    return {
        'total_revisions': total,
        'invalid_revisions': invalid,
        'capitulation_pct': round(pct, 2),
    }


# --- System state (key/value, used for cross-restart counters) ---

def get_system_state(key, default=None):
    """Read a single value from the system_state table.

    Returns the stored string value, or `default` (any type) if the key
    is missing. Numeric callers cast on read (e.g. int(get_system_state(
    'rotation_cycle_counter', '0')))."""
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM system_state WHERE key=?", (key,)
    ).fetchone()
    conn.close()
    return row['value'] if row else default


def set_system_state(key, value):
    """Upsert `key=value` into system_state with updated_at=now."""
    now = datetime.utcnow().isoformat()
    conn = get_conn()
    conn.execute(
        '''INSERT INTO system_state (key, value, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET
               value=excluded.value,
               updated_at=excluded.updated_at''',
        (key, str(value), now),
    )
    conn.commit()
    conn.close()


# --- Memory rotation lifecycle ---

def insert_memory_rotation(agent_id, cycle_number, chars_before,
                           chars_after, patterns_added, status,
                           snapshot_path=None, error_detail=None,
                           degraded_count_in_window=0):
    """Insert a memory_rotations row. Called by
    magi.memory_lifecycle.maybe_rotate once per agent per rotation
    attempt regardless of outcome. Failed rotations are as important to
    track as successful ones, so this is always called from the wrapper.

    degraded_count_in_window: count of last-30 R0 rows that matched
    SAFE_DEFAULTS for this agent. Drives status='skipped_degraded' when
    >= 12 (40% threshold).
    """
    conn = get_conn()
    conn.execute(
        '''INSERT INTO memory_rotations
            (timestamp, agent_id, cycle_number,
             self_model_chars_before, self_model_chars_after,
             patterns_added, status, snapshot_path, error_detail,
             degraded_count_in_window)
           VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (datetime.utcnow().isoformat(), agent_id, int(cycle_number),
         chars_before, chars_after,
         int(patterns_added or 0), status,
         snapshot_path,
         (error_detail or None) if error_detail is None
            else str(error_detail)[:1000],
         int(degraded_count_in_window or 0)),
    )
    conn.commit()
    conn.close()


def get_recent_memory_rotations(limit=10):
    """Return the most recent N memory_rotations rows."""
    conn = get_conn()
    rows = conn.execute(
        '''SELECT id, timestamp, agent_id, cycle_number,
                  self_model_chars_before, self_model_chars_after,
                  patterns_added, status, snapshot_path, error_detail
           FROM memory_rotations
           ORDER BY timestamp DESC LIMIT ?''',
        (int(limit),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    from magi import adam
    adam.init_oneshot("database")
    init_db()
