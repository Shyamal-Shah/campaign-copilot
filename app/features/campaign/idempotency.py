from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


class IdempotencyConflict(Exception):
    """The key is reserved by an in-flight request (maps to HTTP 409)."""


@dataclass
class Reservation:
    """Outcome of trying to reserve a key.

    - ``status == "reserved"``: this caller won the race and must do the work, then ``complete()``.
    - ``status == "completed"``: a prior request finished; return ``response_json`` verbatim.
    """

    status: str  # "reserved" | "completed"
    response_json: str | None = None
    campaign_id: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reserve(conn: sqlite3.Connection, key: str) -> Reservation:
    """Atomically reserve ``key``, or report its existing state.

    Raises :class:`IdempotencyConflict` if a reservation is still ``in_progress``.
    """
    with conn:  # one transaction: the INSERT and its conflict check are atomic
        cur = conn.execute(
            "INSERT OR IGNORE INTO idempotency_keys (key, status, created_at) "
            "VALUES (?, 'in_progress', ?)",
            (key, _now()),
        )
        if cur.rowcount == 1:
            return Reservation(status="reserved")

        row = conn.execute(
            "SELECT status, response_json, campaign_id FROM idempotency_keys WHERE key = ?",
            (key,),
        ).fetchone()

    # Row must exist (we just lost the INSERT race to it).
    if row["status"] == "completed":
        return Reservation(
            status="completed",
            response_json=row["response_json"],
            campaign_id=row["campaign_id"],
        )
    raise IdempotencyConflict(key)


def complete(
    conn: sqlite3.Connection,
    key: str,
    *,
    response_json: str,
    campaign_id: str | None,
) -> None:
    """Mark a reserved key ``completed`` and cache its response for future retries."""
    with conn:
        conn.execute(
            "UPDATE idempotency_keys "
            "SET status = 'completed', response_json = ?, campaign_id = ?, completed_at = ? "
            "WHERE key = ?",
            (response_json, campaign_id, _now(), key),
        )


def release(conn: sqlite3.Connection, key: str) -> None:
    """Drop an ``in_progress`` reservation so a client can retry after a failed run.

    Only deletes rows still in flight — a ``completed`` key is never released. (A process that
    crashes mid-run leaves a stuck ``in_progress`` row; reaping those via a TTL is a known
    limitation, documented in DESIGN.)
    """
    with conn:
        conn.execute(
            "DELETE FROM idempotency_keys WHERE key = ? AND status = 'in_progress'",
            (key,),
        )
