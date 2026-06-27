from __future__ import annotations

import sqlite3

from pydantic import BaseModel

from app.features.segment.compiler import compile_segment
from app.features.segment.dsl import SegmentDefinition


class SegmentUser(BaseModel):
    user_id: str
    lifecycle_stage: str | None
    days_since_app_open: int | None
    is_payer: int


class SegmentResult(BaseModel):
    count: int
    total_users: int
    pct_of_base: float
    empty: bool
    too_broad: bool
    users: list[SegmentUser]  # a small sample of matching users


def preview_segment(
    conn: sqlite3.Connection,
    defn: SegmentDefinition,
    as_of_date: str,
    max_reach: float,
    sample_size: int = 5,
) -> SegmentResult:
    from_sql, where_sql, params = compile_segment(defn, as_of_date)

    count = conn.execute(
        f"SELECT COUNT(*) FROM {from_sql} WHERE {where_sql}", params
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM user_metrics").fetchone()[0]
    sample_rows = conn.execute(
        f"SELECT user_metrics.user_id, user_metrics.lifecycle_stage, "
        f"user_metrics.days_since_app_open, user_metrics.is_payer "
        f"FROM {from_sql} WHERE {where_sql} LIMIT ?",
        [*params, sample_size],
    ).fetchall()

    pct = count / total if total else 0.0
    return SegmentResult(
        count=count,
        total_users=total,
        pct_of_base=round(pct, 4),
        empty=count == 0,
        too_broad=pct > max_reach,
        users=[SegmentUser(**dict(r)) for r in sample_rows],
    )
