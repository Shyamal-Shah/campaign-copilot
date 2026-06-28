from __future__ import annotations

import pytest

from conftest import build_db

from app.core.agent import tools
from app.core.agent.types import PlannerState
from app.core.observability.trace import RunTrace
from app.features.segment.dsl import SegmentDefinition
from app.shared.config import get_settings

US_ONLY = SegmentDefinition.model_validate(
    {"match": "all", "predicates": [{"field": "country", "op": "in", "values": ["US"]}]}
)
EVERYONE = SegmentDefinition.model_validate(
    {
        "match": "all",
        "predicates": [
            {"field": "country", "op": "in", "values": ["US", "IN", "NG", "DE", "BR"]}
        ],
    }
)


def _ctx(conn) -> PlannerState:
    return PlannerState(db=conn, settings=get_settings(), trace=RunTrace())


def _args(**over) -> tools.CreateCampaignArgs:
    # No segment, and a FLAT message — create_campaign reads the segment from PlannerState and
    # assembles the channel-appropriate message object itself.
    return tools.CreateCampaignArgs.model_validate(
        {
            "name": "Winback US",
            "channel": "push",
            "title": "We miss you",
            "body": "Here's 10% off",
            "cited_guidelines": ["07"],
            **over,
        }
    )


@pytest.fixture
def conn(tmp_path):
    return build_db(tmp_path)



def test_query_segment_returns_grounded_count(conn):
    out = tools.query_segment(_ctx(conn), US_ONLY)
    assert out["count"] == 3
    assert out["total_users"] == 10
    assert out["too_broad"] is False


def test_create_campaign_grounds_size_and_dedupes(conn):
    ctx = _ctx(conn)
    ctx.segment = US_ONLY  # query_segment would have set this from the sized audience
    out = tools.create_campaign(ctx, _args())
    assert out["status"] == "created"
    assert out["segment_size"] == 3
    assert ctx.campaign_id == out["campaign_id"]
    # Second call in the same run does not create a duplicate.
    again = tools.create_campaign(ctx, _args())
    assert again["status"] == "already_created"
    assert again["campaign_id"] == out["campaign_id"]


def test_create_campaign_rejects_too_broad_segment(conn):
    ctx = _ctx(conn)
    ctx.segment = EVERYONE  # reach-cap is a hard block at create time, even if state holds it
    out = tools.create_campaign(ctx, _args())
    assert out["status"] == "error"
    assert out["error"] == "segment_too_broad"


def test_create_campaign_without_a_segment_errors(conn):
    # No query_segment ran, so there's no grounded audience — create must refuse.
    out = tools.create_campaign(_ctx(conn), _args())
    assert out["status"] == "error"
    assert out["error"] == "no_segment"


def test_create_campaign_flags_push_limit_violation(conn):
    # A push title over the doc-03 limit comes back as a typed, correctable error (not a crash).
    ctx = _ctx(conn)
    ctx.segment = US_ONLY
    out = tools.create_campaign(ctx, _args(title="x" * 80))
    assert out["status"] == "error"
    assert out["error"] == "invalid_message"


def test_search_guidelines_returns_cited_docs(conn, recorded_guidelines):
    out = tools.search_guidelines(
        _ctx(conn), tools.SearchGuidelinesArgs(query=recorded_guidelines)
    )
    assert out["results"]
    assert all(r["doc_id"] for r in out["results"])
