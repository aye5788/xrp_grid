"""Property-based test suite (Hypothesis) over the light-import money-path
modules — the CI half of the 2026-07-02 proactive bug-catching plan (see
02_NEXT_BUILD_TASKS.md PLAN block).

Design rules:
  * PROPERTIES, not cases: each test states a rule the module must satisfy for
    ANY input Hypothesis generates — the forward-looking layer that explores
    states nobody enumerated (the empty-book / emoji-title / duplicate-ballot
    class of bug).
  * Light imports only: magi.notify, magi.agents.aggregate, grid.forward_sim,
    grid.pnl. Engine/scheduler integration properties need the full dep chain
    + DB fixtures and belong to the drill layer, not CI.
  * NO live side effects: anything that could write (ntfy POST, magi_alerts)
    is monkeypatched — this suite must be safe to run on the droplet next to
    the live observer.db.

Each property is annotated with the real 2026 bug class it guards against.
"""

import string

import pytest
from hypothesis import given, settings, strategies as st

# ---------------------------------------------------------------- notify ----
# Bug class guarded: 2026-06-27..07-02 — an emoji in the ntfy title raised
# UnicodeEncodeError inside requests (HTTP headers are latin-1) and the wake
# notification silently never delivered, for five days.


class _FakeResp:
    status_code = 200
    text = ""


@settings(max_examples=200, deadline=None)
@given(title=st.text(min_size=0, max_size=200))
def test_send_ntfy_survives_any_unicode_title(title):
    import magi.notify as notify

    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        # requests encodes header values latin-1; a non-encodable value raises
        # BEFORE any network I/O. Reproduce that contract exactly.
        for k, v in (headers or {}).items():
            k.encode("latin-1")
            v.encode("latin-1")
        captured["headers"] = headers
        return _FakeResp()

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("NTFY_TOPIC_URL", "https://ntfy.sh/test-topic")
        mp.setattr(notify.requests, "post", fake_post)
        ok = notify.send_ntfy(title, "body", "critical")
    assert ok is True
    assert captured["headers"]["Title"].strip() != ""


# ------------------------------------------------------------- aggregate ----
# Bug class guarded: 2026-07-02 — a duplicated ranking label double-scored in
# Borda and flipped pairwise positions, silently distorting the council tally.

_LABELS = ["A", "B", "C"]


class _Cand:
    def __init__(self, action):
        self.action = action


class _Rank:
    def __init__(self, order):
        self.order = order


def _cands():
    return {lb: _Cand("MAINTAIN") for lb in _LABELS}


_ballot = st.lists(
    st.sampled_from(_LABELS + ["X", "Y", ""]), min_size=0, max_size=8
)


@settings(max_examples=300, deadline=None)
@given(orders=st.lists(_ballot, min_size=0, max_size=5))
def test_aggregate_never_crashes_and_winner_is_presented(orders):
    import database
    from magi.agents import aggregate as agg

    # The sanitizer alerts on excluded ballots via database.insert_alert —
    # stub it so property runs NEVER write to the live observer.db.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(database, "insert_alert", lambda *a, **k: None)
        result = agg.aggregate([_Rank(o) for o in orders], _cands())
    assert result["winner_label"] in (None, *_LABELS)
    assert (result["winner"] is None) == (result["winner_label"] is None)


@settings(max_examples=100, deadline=None)
@given(perm=st.permutations(_LABELS))
def test_sanitizer_passes_wellformed_ballots_through(perm):
    from magi.agents import aggregate as agg

    clean = agg._sanitize_rankings([_Rank(list(perm))], _LABELS)
    assert len(clean) == 1
    assert list(clean[0].order) == list(perm)


@settings(max_examples=200, deadline=None)
@given(order=_ballot)
def test_sanitizer_output_is_permutation_or_excluded(order):
    import database
    from magi.agents import aggregate as agg

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(database, "insert_alert", lambda *a, **k: None)
        clean = agg._sanitize_rankings([_Rank(order)], _LABELS)
    assert len(clean) in (0, 1)
    if clean:
        assert sorted(clean[0].order) == sorted(_LABELS)


# ------------------------------------------------------------ forward_sim ---
# Bug class guarded: 2026-07-02 — two graders scored the same STAND_ASIDE row
# by different predicates; the shared helpers are the single definition and
# must be total over garbage inputs.


@settings(max_examples=300, deadline=None)
@given(
    spacing=st.one_of(
        st.none(),
        st.floats(min_value=-1, max_value=1, allow_nan=False),
        st.text(alphabet=string.ascii_letters, max_size=3),
    ),
    levels=st.one_of(st.none(), st.integers(min_value=-5, max_value=50),
                     st.text(alphabet=string.ascii_letters, max_size=3)),
)
def test_stance_band_is_total_and_positive(spacing, levels):
    from grid.forward_sim import stance_band

    band = stance_band(spacing, levels)
    assert band > 0
    # Garbage / missing geometry must land exactly on the documented fallback.
    try:
        legit = float(spacing or 0) > 0
    except (TypeError, ValueError):
        legit = False
    if not legit:
        assert band == 0.05


@settings(max_examples=300, deadline=None)
@given(
    closes=st.lists(st.floats(min_value=0.001, max_value=1000,
                              allow_nan=False), max_size=100),
    price=st.floats(min_value=0.001, max_value=1000, allow_nan=False),
    band=st.floats(min_value=0.0, max_value=2.0, allow_nan=False),
)
def test_path_breaks_matches_its_definition(closes, price, band):
    from grid.forward_sim import path_breaks

    down, up = path_breaks(closes, price, band)
    if not closes:
        assert (down, up) == (False, False)
    else:
        assert down == (min(closes) < price * (1 - band))
        assert up == (max(closes) > price * (1 + band))


# -------------------------------------------------------------------- pnl ---
# Bug class guarded: 2026-05/06 — PnL commingling and scope bugs; the FIFO
# matcher is the harvest metric's core and must be total + conservation-sane
# for ANY fill sequence.

_fill = st.fixed_dictionaries({
    "side": st.sampled_from(["buy", "sell"]),
    "order_id": st.text(alphabet=string.hexdigits, min_size=1, max_size=8),
    "price": st.one_of(st.none(), st.floats(min_value=0.0001, max_value=1000,
                                            allow_nan=False)),
    "fill_price": st.one_of(st.none(), st.floats(min_value=0.0001,
                                                 max_value=1000,
                                                 allow_nan=False)),
    "size": st.one_of(st.none(), st.floats(min_value=0, max_value=100,
                                           allow_nan=False)),
    "fee": st.one_of(st.none(), st.floats(min_value=0, max_value=10,
                                          allow_nan=False)),
})


@settings(max_examples=300, deadline=None)
@given(fills=st.lists(_fill, max_size=40))
def test_fifo_match_is_total_and_conserves_counts(fills):
    from grid.pnl import _fifo_match

    trips, unmatched = _fifo_match(fills)
    n_buys = sum(1 for f in fills if f["side"] == "buy")
    n_sells = sum(1 for f in fills if f["side"] == "sell")
    # A sell can be split across several buys, but there can never be MORE
    # matched trips than (buys + sells), and never a trip without both sides.
    assert len(trips) <= n_buys + n_sells
    assert len(unmatched) <= n_buys
    for t in trips:
        assert t["size"] >= 0
        assert t["contribution"] == pytest.approx(t["contribution"])  # finite
