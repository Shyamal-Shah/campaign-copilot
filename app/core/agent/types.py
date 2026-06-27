from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from app.core.observability.trace import RunTrace
from app.features.campaign.models import CampaignDraft
from app.shared.config import Settings


@dataclass
class AgentContext:
    db: sqlite3.Connection
    settings: Settings
    trace: RunTrace
    campaign_id: str | None = (
        None  # set once create_campaign runs (run-level dedupe + result plumbing)
    )


class CopilotOutcome(BaseModel):
    status: Literal["created", "unsupported", "needs_clarification"]
    campaign: CampaignDraft | None = None
    message: str = ""
    reason: str | None = None
