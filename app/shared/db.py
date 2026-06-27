"""Application database: our own state plus derived read-models.

Kept entirely separate from the read-only source dataset (``source.py``) so the provided file stays
pristine and our DB can be rebuilt from scratch on every boot. Pass ``":memory:"`` for tests.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
-- Created campaigns.
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id              TEXT PRIMARY KEY,
    name                     TEXT,
    goal                     TEXT,
    channel                  TEXT,
    status                   TEXT DEFAULT 'draft',
    segment_definition_json  TEXT,
    segment_size             INTEGER,
    message_json             TEXT,
    offer_json               TEXT,
    image_url                TEXT,
    idempotency_key          TEXT UNIQUE,
    trace_id                 TEXT,
    created_at               TEXT
);

-- Idempotency reservations.
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key            TEXT PRIMARY KEY,
    status         TEXT,                 -- in_progress | completed
    request_hash   TEXT,
    campaign_id    TEXT,
    response_json  TEXT,
    created_at     TEXT,
    completed_at   TEXT
);

-- One row per copilot run; the persisted trace for debugging a bad run.
CREATE TABLE IF NOT EXISTS runs (
    trace_id      TEXT PRIMARY KEY,
    goal          TEXT,
    status        TEXT,
    degraded      INTEGER DEFAULT 0,
    total_ms      INTEGER,
    total_tokens  INTEGER,
    est_cost      REAL,
    campaign_id   TEXT,
    steps_json    TEXT,
    created_at    TEXT
);

-- Derived per-user behavioral read-model, rebuilt from events at startup.
CREATE TABLE IF NOT EXISTS user_metrics (
    user_id               TEXT PRIMARY KEY,
    last_app_open         TEXT,
    last_session_start    TEXT,
    last_any_event        TEXT,
    days_since_app_open   INTEGER,
    days_since_any_event  INTEGER,
    days_since_signup     INTEGER,
    app_open_count_30d    INTEGER,
    session_count_30d     INTEGER,
    purchase_count        INTEGER,
    is_payer              INTEGER,
    total_events          INTEGER,
    lifecycle_stage       TEXT
);

-- Normalized feature-adoption set: one row per (user, feature) used.
CREATE TABLE IF NOT EXISTS user_features (
    user_id  TEXT,
    feature  TEXT,
    PRIMARY KEY (user_id, feature)
);

-- Small key/value table for build fingerprints etc.
CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE INDEX IF NOT EXISTS idx_user_metrics_dsa      ON user_metrics(days_since_app_open);
CREATE INDEX IF NOT EXISTS idx_user_metrics_stage    ON user_metrics(lifecycle_stage);
CREATE INDEX IF NOT EXISTS idx_user_features_feature ON user_features(feature);
"""


def connect_app(path: str) -> sqlite3.Connection:
    """Open (creating if needed) the app database. ``check_same_thread=False`` so FastAPI's threadpool
    can share the one connection."""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables/indexes if absent (idempotent)."""
    conn.executescript(SCHEMA)
    conn.commit()
