from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from app.features.segment import lifecycle
from app.shared.source import connect_source

_AGG_SQL = """
SELECT
    u.user_id                                                                         AS user_id,
    u.signup_date                                                                     AS signup_date,
    MAX(CASE WHEN e.event_name='app_open'      THEN e.timestamp END)                  AS last_app_open,
    MAX(CASE WHEN e.event_name='session_start' THEN e.timestamp END)                  AS last_session_start,
    MAX(e.timestamp)                                                                  AS last_any_event,
    SUM(CASE WHEN e.event_name='app_open'      AND e.timestamp >= :w THEN 1 ELSE 0 END) AS app_open_count_30d,
    SUM(CASE WHEN e.event_name='session_start' AND e.timestamp >= :w THEN 1 ELSE 0 END) AS session_count_30d,
    SUM(CASE WHEN e.event_name='purchase'      THEN 1 ELSE 0 END)                     AS purchase_count,
    COUNT(e.event_id)                                                                 AS total_events
FROM users u
LEFT JOIN events e ON e.user_id = u.user_id   -- LEFT JOIN: never-active users still get a row
GROUP BY u.user_id
"""

_FEATURES_SQL = """
SELECT DISTINCT user_id, LOWER(json_extract(properties, '$.feature_name')) AS feature
FROM events
WHERE event_name = 'feature_used'
  AND json_extract(properties, '$.feature_name') IS NOT NULL
"""

_USERS_SQL = """
SELECT user_id, signup_date, LOWER(country) AS country, LOWER(platform) AS platform,
       app_version, LOWER(plan) AS plan
FROM users
"""


def _days_since(as_of: date, ts: str | None) -> int | None:
    """Whole days from a timestamp's date to the as-of date; ``None`` if the timestamp is absent."""
    if not ts:
        return None
    return (as_of - date.fromisoformat(ts[:10])).days


def build_metrics(
    app_conn: sqlite3.Connection, source_path: str, as_of_date: str
) -> int:
    """(Re)build ``user_metrics`` + ``user_features`` from the source events. Returns the row count."""
    as_of = date.fromisoformat(as_of_date)
    window_30d = (as_of - timedelta(days=30)).isoformat()

    src = connect_source(source_path)
    try:
        agg_rows = src.execute(_AGG_SQL, {"w": window_30d}).fetchall()
        feature_rows = src.execute(_FEATURES_SQL).fetchall()
        user_rows = src.execute(_USERS_SQL).fetchall()
    finally:
        src.close()

    metrics = []
    for r in agg_rows:
        dsa = _days_since(as_of, r["last_app_open"])
        dse = _days_since(as_of, r["last_any_event"])
        dss = _days_since(as_of, r["signup_date"])
        purchases = r["purchase_count"] or 0
        metrics.append(
            (
                r["user_id"],
                r["last_app_open"],
                r["last_session_start"],
                r["last_any_event"],
                dsa,
                dse,
                dss,
                r["app_open_count_30d"] or 0,
                r["session_count_30d"] or 0,
                purchases,
                1 if purchases > 0 else 0,
                r["total_events"] or 0,
                lifecycle.compute_stage(dsa, dss),
            )
        )

    with app_conn:  # single transaction; full rebuild
        app_conn.execute("DELETE FROM users")
        app_conn.execute("DELETE FROM user_metrics")
        app_conn.execute("DELETE FROM user_features")
        app_conn.executemany(
            "INSERT INTO users (user_id, signup_date, country, platform, app_version, plan) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [tuple(r) for r in user_rows],
        )
        app_conn.executemany(
            """
            INSERT INTO user_metrics (
                user_id, last_app_open, last_session_start, last_any_event,
                days_since_app_open, days_since_any_event, days_since_signup,
                app_open_count_30d, session_count_30d, purchase_count, is_payer,
                total_events, lifecycle_stage
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            metrics,
        )
        app_conn.executemany(
            "INSERT OR IGNORE INTO user_features (user_id, feature) VALUES (?, ?)",
            [(f["user_id"], f["feature"]) for f in feature_rows],
        )
    return len(metrics)


def ensure_metrics(
    app_conn: sqlite3.Connection, source_path: str, as_of_date: str
) -> int:
    """Build the read-models the first time the app DB has none; reuse them on later boots.

    Returns the ``user_metrics`` row count. The build is transactional, so a partial/interrupted
    build leaves the tables empty and is retried on the next boot.
    """
    existing = count_user_metrics(app_conn)
    if existing:
        return existing
    return build_metrics(app_conn, source_path, as_of_date)


def count_user_metrics(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM user_metrics").fetchone()[0]


def count_payers(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM user_metrics WHERE is_payer = 1"
    ).fetchone()[0]


def count_active(conn: sqlite3.Connection, days: int = 14) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM user_metrics WHERE days_since_app_open <= ?", (days,)
    ).fetchone()[0]
