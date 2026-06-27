from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from pydantic import TypeAdapter

from app.features.campaign.models import Campaign, CampaignDraft, CampaignMessage
from app.features.segment.service import preview_segment

_MESSAGE_ADAPTER = TypeAdapter(CampaignMessage)


class SegmentTooBroad(Exception):
    """The draft's segment exceeds ``max_segment_reach`` (maps to HTTP 400)."""

    def __init__(self, pct: float, max_reach: float):
        self.pct = pct
        self.max_reach = max_reach
        super().__init__(
            f"segment reaches {pct:.1%} of the base (cap {max_reach:.0%}); narrow it before creating"
        )


def create_campaign(
    conn: sqlite3.Connection,
    draft: CampaignDraft,
    *,
    as_of_date: str,
    max_reach: float,
    trace_id: str | None = None,
) -> Campaign:
    """Insert a campaign, grounding ``segment_size`` in a real query and enforcing the reach cap."""
    segment = preview_segment(conn, draft.segment, as_of_date, max_reach)
    if segment.too_broad:
        raise SegmentTooBroad(segment.pct_of_base, max_reach)

    campaign_id = f"camp_{uuid.uuid4().hex[:16]}"
    created_at = datetime.now(timezone.utc).isoformat()
    message_json = draft.message.model_dump_json()
    offer_json = draft.offer.model_dump_json() if draft.offer else None

    with conn:
        conn.execute(
            """
            INSERT INTO campaigns (
                campaign_id, name, goal, channel, status, segment_definition_json,
                segment_size, message_json, offer_json, image_url, cited_guidelines_json,
                trace_id, created_at
            ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                campaign_id,
                draft.name,
                draft.goal,
                draft.channel,
                draft.segment.model_dump_json(),
                segment.count,
                message_json,
                offer_json,
                draft.image_url,
                json.dumps(draft.cited_guidelines),
                trace_id,
                created_at,
            ),
        )

    return Campaign(
        campaign_id=campaign_id,
        name=draft.name,
        goal=draft.goal,
        channel=draft.channel,
        status="draft",
        segment_size=segment.count,
        segment=draft.segment,
        message=draft.message,
        offer=draft.offer,
        image_url=draft.image_url,
        cited_guidelines=draft.cited_guidelines,
        trace_id=trace_id,
        created_at=created_at,
    )


def _row_to_campaign(row: sqlite3.Row) -> Campaign:
    message = _MESSAGE_ADAPTER.validate_json(row["message_json"])
    offer = json.loads(row["offer_json"]) if row["offer_json"] else None
    citations = (
        json.loads(row["cited_guidelines_json"]) if row["cited_guidelines_json"] else []
    )
    return Campaign(
        campaign_id=row["campaign_id"],
        name=row["name"],
        goal=row["goal"],
        channel=row["channel"],
        status=row["status"],
        segment_size=row["segment_size"] or 0,
        segment=json.loads(row["segment_definition_json"]),
        message=message,
        offer=offer,
        image_url=row["image_url"],
        cited_guidelines=citations,
        trace_id=row["trace_id"],
        created_at=row["created_at"],
    )


def get_campaign(conn: sqlite3.Connection, campaign_id: str) -> Campaign | None:
    row = conn.execute(
        "SELECT * FROM campaigns WHERE campaign_id = ?", (campaign_id,)
    ).fetchone()
    return _row_to_campaign(row) if row else None


def list_campaigns(conn: sqlite3.Connection, limit: int = 50) -> list[Campaign]:
    rows = conn.execute(
        "SELECT * FROM campaigns ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row_to_campaign(r) for r in rows]
