"""M1: the behavioural read-model build is correct — verified by logic, not by hardcoded counts.

Pure unit tests: every case builds from a controlled, synthetic source (no real dataset, no global
settings), so they stay hermetic and robust to the provided data changing. Three angles:
  1. cross-check the build against an *independent* recomputation from the source (no magic numbers);
  2. assert invariants that must hold for every row regardless of the data;
  3. assert exact, known-by-construction values on a tiny synthetic source (the edge cases).
"""

import sqlite3
from datetime import date, timedelta

from app.features.segment import lifecycle
from app.features.segment.metrics import (
    build_metrics,
    count_active,
    count_payers,
    count_user_metrics,
    ensure_metrics,
)
from app.shared.db import connect_app, init_schema
from app.shared.source import connect_source

# A controlled source with enough variety to exercise the build: payers vs non-payers, recently-active
# vs lapsed vs never-active, a recent signup, and a repeated feature (dedup). The tests derive every
# count from this source rather than hardcoding it, so the dataset can change freely.
_AS_OF = "2026-06-24"
_USERS = [
    ("u_active_payer", "2025-01-01", "US", "iOS", "3.4.0", "pro"),
    ("u_active_free", "2025-03-01", "IN", "Android", "3.4.0", "free"),
    ("u_lapsed_payer", "2024-01-01", "GB", "iOS", "3.2.0", "pro"),
    ("u_never", "2025-01-01", "US", "Web", "3.0.0", "free"),
    ("u_new", "2026-06-22", "DE", "iOS", "3.4.0", "free"),
]
_EVENTS = [
    ("e1", "u_active_payer", "app_open", "2026-06-23T09:00:00Z", "{}"),
    ("e2", "u_active_payer", "purchase", "2026-06-20T09:00:00Z", '{"amount": 4900}'),
    ("e3", "u_active_payer", "feature_used", "2026-06-20T09:05:00Z", '{"feature_name": "voice_agent"}'),
    ("e4", "u_active_payer", "feature_used", "2026-06-21T09:05:00Z", '{"feature_name": "voice_agent"}'),
    ("e5", "u_active_free", "app_open", "2026-06-22T09:00:00Z", "{}"),
    ("e6", "u_active_free", "session_start", "2026-06-22T09:01:00Z", "{}"),
    ("e7", "u_lapsed_payer", "app_open", "2026-06-01T09:00:00Z", "{}"),  # 23d ago → not active(14)
    ("e8", "u_lapsed_payer", "purchase", "2026-05-15T09:00:00Z", '{"amount": 9900}'),
    # u_never has no events at all; u_new only just opened the app
    ("e9", "u_new", "app_open", "2026-06-23T12:00:00Z", "{}"),
]


def _build_synthetic(tmp_path):
    """Build the read-models from the controlled synthetic source above (hermetic; no real data)."""
    source = _synthetic_source(tmp_path, _USERS, _EVENTS)
    conn = connect_app(":memory:")
    init_schema(conn)
    build_metrics(conn, source, _AS_OF)
    return conn, source


def test_build_matches_independent_recomputation(tmp_path):
    """Every user gets one row, and payer/active counts match a different query over the source."""
    conn, source = _build_synthetic(tmp_path)
    threshold = (date.fromisoformat(_AS_OF) - timedelta(days=14)).isoformat()

    src = connect_source(source)
    try:
        n_users = src.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        # recomputed via a different path than the build (DISTINCT over events, not per-user rollup)
        payers = src.execute(
            "SELECT COUNT(DISTINCT user_id) FROM events WHERE event_name='purchase'"
        ).fetchone()[0]
        active = src.execute(
            "SELECT COUNT(DISTINCT user_id) FROM events "
            "WHERE event_name='app_open' AND timestamp >= ?",
            (threshold,),
        ).fetchone()[0]
    finally:
        src.close()

    assert count_user_metrics(conn) == n_users  # exactly one metrics row per user
    assert count_payers(conn) == payers
    assert count_active(conn, 14) == active


def test_built_rows_satisfy_invariants(tmp_path):
    """Per-row properties that hold for any dataset."""
    conn, _ = _build_synthetic(tmp_path)

    # is_payer is exactly "has at least one purchase"
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM user_metrics WHERE (is_payer = 1) != (purchase_count > 0)"
        ).fetchone()[0]
        == 0
    )
    # recency is NULL iff the user never did the action, and never negative otherwise
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM user_metrics "
            "WHERE (days_since_app_open IS NULL) != (last_app_open IS NULL)"
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM user_metrics WHERE days_since_app_open < 0").fetchone()[0]
        == 0
    )
    # lifecycle_stage on the row always equals the single source of truth
    for r in conn.execute(
        "SELECT days_since_app_open, days_since_signup, lifecycle_stage FROM user_metrics"
    ):
        assert r["lifecycle_stage"] == lifecycle.compute_stage(
            r["days_since_app_open"], r["days_since_signup"]
        )
    # feature adoption only carries non-empty feature names
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM user_features WHERE feature IS NULL OR feature = ''"
        ).fetchone()[0]
        == 0
    )


def _synthetic_source(tmp_path, users, events) -> str:
    path = tmp_path / "source.sqlite"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE users (user_id TEXT PRIMARY KEY, signup_date TEXT, country TEXT, "
        "platform TEXT, app_version TEXT, plan TEXT)"
    )
    con.execute(
        "CREATE TABLE events (event_id TEXT PRIMARY KEY, user_id TEXT, event_name TEXT, "
        "timestamp TEXT, properties TEXT)"
    )
    con.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)", users)
    con.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?)", events)
    con.commit()
    con.close()
    return str(path)


def test_build_logic_on_synthetic_data(tmp_path):
    """Exact assertions on controlled data — the edge cases, known by construction (no magic numbers)."""
    as_of = "2026-06-24"
    users = [
        ("u_new", "2026-06-20", "US", "iOS", "3.4.0", "free"),       # signed up 4 days ago
        ("u_payer", "2025-01-01", "IN", "Android", "3.4.0", "pro"),  # active-ish payer
        ("u_never", "2025-01-01", "US", "Web", "3.0.0", "free"),     # no events at all
    ]
    events = [
        ("e1", "u_new", "app_open", "2026-06-23T10:00:00Z", "{}"),
        ("e2", "u_payer", "app_open", "2026-06-01T10:00:00Z", "{}"),
        ("e3", "u_payer", "purchase", "2026-06-02T10:00:00Z", '{"amount": 4900}'),
        ("e4", "u_payer", "feature_used", "2026-06-02T10:00:00Z", '{"feature_name": "voice_agent"}'),
        ("e5", "u_payer", "feature_used", "2026-06-03T10:00:00Z", '{"feature_name": "voice_agent"}'),
    ]
    source = _synthetic_source(tmp_path, users, events)
    conn = connect_app(":memory:")
    init_schema(conn)
    n = build_metrics(conn, source, as_of)

    assert n == 3  # one row per user, including the never-active one (LEFT JOIN)
    rows = {r["user_id"]: r for r in conn.execute("SELECT * FROM user_metrics")}

    # never-active user: null recency, not a payer, churned
    assert rows["u_never"]["days_since_app_open"] is None
    assert rows["u_never"]["is_payer"] == 0
    assert rows["u_never"]["lifecycle_stage"] == "churned"

    # payer: flagged, recency is the date diff to as-of (2026-06-24 − 2026-06-01 = 23)
    assert rows["u_payer"]["is_payer"] == 1
    assert rows["u_payer"]["purchase_count"] == 1
    assert rows["u_payer"]["days_since_app_open"] == 23

    # recent signup → "new"
    assert rows["u_new"]["lifecycle_stage"] == "new"

    # feature adoption is deduped: voice_agent recorded once for u_payer
    feats = sorted(
        (r["user_id"], r["feature"]) for r in conn.execute("SELECT user_id, feature FROM user_features")
    )
    assert feats == [("u_payer", "voice_agent")]


def test_ensure_metrics_builds_once(tmp_path):
    """First call populates the empty tables; a later call reuses them instead of rebuilding."""
    source = _synthetic_source(tmp_path, [("u1", "2025-01-01", "US", "iOS", "3.4.0", "free")], [])
    conn = connect_app(":memory:")
    init_schema(conn)

    assert ensure_metrics(conn, source, "2026-06-24") == 1

    # tag the existing row; a second ensure must leave it untouched (i.e. not rebuild)
    conn.execute("UPDATE user_metrics SET total_events = 999")
    conn.commit()
    assert ensure_metrics(conn, source, "2026-06-24") == 1
    assert conn.execute("SELECT total_events FROM user_metrics").fetchone()[0] == 999
