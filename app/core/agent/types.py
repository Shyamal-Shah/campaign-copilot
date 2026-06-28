from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.core.observability.trace import RunTrace
from app.features.segment.dsl import SegmentDefinition
from app.shared.config import Settings


@dataclass
class PlannerState:
    """Typed run-memory threaded through the agent the SDK run context, never sent to the LLM. 
    Tools read/write it, and the run's outcome is *derived from it* rather than from the
    model's free text: a campaign counts as "created" only when ``create_campaign`` actually
    persisted one (``campaign_id`` set), and a decline is real only when ``finish`` recorded it.
    """

    db: sqlite3.Connection
    settings: Settings
    requested_name: str | None = None
    channel_hint: str | None = None
    segment: SegmentDefinition | None = None
    campaign_id: str | None = None
    # set by the finish tool when the run declines: "unsupported" | "needs_clarification"
    finish_status: str | None = None
    finish_message: str = ""  # the decline reason / clarifying question
