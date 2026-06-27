from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


def new_trace_id() -> str:
    return f"run_{uuid.uuid4().hex}"


class Step(BaseModel):
    seq: int
    kind: str  # "tool" | "model" | "note"
    name: str
    status: str  # "ok" | "error" | "empty"
    latency_ms: float | None = None
    summary: str = ""
    detail: dict | None = None


class RunTrace(BaseModel):
    """Mutable, in-memory record of one copilot run."""

    trace_id: str = Field(default_factory=new_trace_id)
    goal: str = ""
    status: str = (
        "in_progress"  # in_progress | created | unsupported | needs_clarification | error
    )
    degraded: bool = False
    campaign_id: str | None = None
    total_ms: float | None = None
    total_tokens: int = 0
    total_requests: int = 0
    est_cost: float = 0.0
    message: str = ""
    steps: list[Step] = Field(default_factory=list)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def add_step(
        self,
        kind: str,
        name: str,
        status: str,
        *,
        latency_ms: float | None = None,
        summary: str = "",
        detail: dict | None = None,
    ) -> Step:
        step = Step(
            seq=len(self.steps) + 1,
            kind=kind,
            name=name,
            status=status,
            latency_ms=round(latency_ms, 2) if latency_ms is not None else None,
            summary=summary,
            detail=detail,
        )
        self.steps.append(step)
        return step


def persist(conn: sqlite3.Connection, trace: RunTrace) -> None:
    """Write (or replace) the trace row."""
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO runs (
                trace_id, goal, status, degraded, total_ms, total_tokens, est_cost,
                campaign_id, steps_json, message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace.trace_id,
                trace.goal,
                trace.status,
                1 if trace.degraded else 0,
                round(trace.total_ms, 2) if trace.total_ms is not None else None,
                trace.total_tokens,
                trace.est_cost,
                trace.campaign_id,
                json.dumps([s.model_dump() for s in trace.steps]),
                trace.message,
                trace.created_at,
            ),
        )


def get_run(conn: sqlite3.Connection, trace_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM runs WHERE trace_id = ?", (trace_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["degraded"] = bool(out["degraded"])
    out["steps"] = json.loads(out.pop("steps_json") or "[]")
    return out
