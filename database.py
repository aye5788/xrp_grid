import sqlite3
import json
from datetime import datetime, date
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
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
        level_count INTEGER NOT NULL UNIQUE,
        state_blob TEXT,
        fill_count INTEGER DEFAULT 0,
        rolling_pnl_pct REAL DEFAULT 0,
        updated_at TEXT
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
        skew_delta_6h REAL,
        grid_alive_6h INTEGER,

        outcome_1h_backfilled INTEGER DEFAULT 0,
        outcome_6h_backfilled INTEGER DEFAULT 0,
        outcome_24h_backfilled INTEGER DEFAULT 0,

        hard_rule_overrides TEXT
    )''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_debate_records_cycle_id
        ON debate_records (cycle_id)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_debate_records_timestamp
        ON debate_records (timestamp)''')

    # Future-proof ALTERs for debate_records (idempotent — match the
    # try/except pattern used above for magi_decisions).
    for _alter in (
        "ALTER TABLE debate_records ADD COLUMN hard_rule_overrides TEXT",
    ):
        try:
            c.execute(_alter)
        except sqlite3.OperationalError:
            pass

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
    """Return (high, low) of the most recent completed candle,
    or (None, None) if no candles exist."""
    conn = get_conn()
    row = conn.execute(
        '''SELECT high, low FROM candles
           WHERE timeframe=?
           ORDER BY id DESC LIMIT 1''',
        (timeframe,)
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

def upsert_shadow_grid_state(level_count, state_dict, fill_count=0, rolling_pnl_pct=0.0):
    conn = get_conn()
    conn.execute('''INSERT INTO shadow_grid_state
        (level_count, state_blob, fill_count, rolling_pnl_pct, updated_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(level_count) DO UPDATE SET
            state_blob=excluded.state_blob,
            fill_count=excluded.fill_count,
            rolling_pnl_pct=excluded.rolling_pnl_pct,
            updated_at=excluded.updated_at''',
        (level_count, json.dumps(state_dict), fill_count, rolling_pnl_pct,
         datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_shadow_grid_state(level_count):
    conn = get_conn()
    row = conn.execute('SELECT state_blob FROM shadow_grid_state WHERE level_count=?',
        (level_count,)).fetchone()
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
    rows = conn.execute('''SELECT level_count, fill_count, rolling_pnl_pct, updated_at
        FROM shadow_grid_state ORDER BY level_count''').fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_best_shadow_from_db():
    """Return (level_count, rolling_pnl_pct) for best shadow variant with fills > 0."""
    rows = get_all_shadow_states()
    candidates = [r for r in rows if (r['fill_count'] or 0) > 0]
    if not candidates:
        return None, None
    best = max(candidates, key=lambda r: r['rolling_pnl_pct'] or 0)
    return best['level_count'], best['rolling_pnl_pct']


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
    rows = conn.execute('''SELECT agent, model,
        SUM(prompt_tokens) as prompt_tokens,
        SUM(completion_tokens) as completion_tokens,
        SUM(total_tokens) as total_tokens,
        SUM(estimated_cost_usd) as cost,
        COUNT(*) as calls
        FROM token_usage WHERE timestamp > ?
        GROUP BY agent, model''', (cutoff,)).fetchall()
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
                            skew_delta=None, grid_alive=None):
    """
    Backfill outcome metrics on a debate_records row.
    window is one of '1h', '6h', '24h'. Sets fills_{window},
    pnl_{window}, outcome_{window}_backfilled=1. For the 6h window also
    optionally sets skew_delta_6h and grid_alive_6h.
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

    vals.append(cycle_id)
    conn = get_conn()
    conn.execute(
        f"UPDATE debate_records SET {', '.join(sets)} WHERE cycle_id=?",
        vals
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


def get_recent_debate_records(limit=20):
    """Return the most recent N debate_records ordered by timestamp DESC."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM debate_records ORDER BY timestamp DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_agent_accuracy(agent_id, days=7):
    """
    Return {total_calls, positive_outcomes, accuracy_pct} for the agent's
    r0 calls over the last `days` days. A 'positive outcome' is
    fills_6h > 0 AND pnl_6h >= 0. Only counts rows where the agent's
    r0_position is non-null and the 6h outcome has been backfilled.
    """
    if agent_id not in _VALID_AGENT_IDS:
        raise ValueError(f"unknown agent_id: {agent_id!r}")

    from datetime import timedelta
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    pos_col = f"{agent_id}_r0_position"

    conn = get_conn()
    rows = conn.execute(
        f'''SELECT {pos_col} AS position, fills_6h, pnl_6h
            FROM debate_records
            WHERE timestamp >= ?
              AND outcome_6h_backfilled=1
              AND {pos_col} IS NOT NULL''',
        (cutoff,)
    ).fetchall()
    conn.close()

    total = len(rows)
    positive = sum(
        1 for r in rows
        if (r['fills_6h'] or 0) > 0
        and r['pnl_6h'] is not None and r['pnl_6h'] >= 0
    )
    accuracy_pct = (positive / total * 100.0) if total > 0 else 0.0
    return {
        'total_calls': total,
        'positive_outcomes': positive,
        'accuracy_pct': round(accuracy_pct, 2),
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


if __name__ == "__main__":
    init_db()
