from __future__ import annotations

import pytest

from conftest import build_db

from app.core.agent import tools
from app.core.agent.types import AgentContext
from app.core.observability.trace import RunTrace
from app.features.campaign.models import CampaignDraft
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


def _ctx(conn) -> AgentContext:
    return AgentContext(db=conn, settings=get_settings(), trace=RunTrace())


def _draft(segment=US_ONLY) -> CampaignDraft:
    return CampaignDraft.model_validate(
        {
            "name": "Winback US",
            "goal": "win back US users",
            "segment": segment.model_dump(),
            "message": {
                "kind": "push",
                "title": "We miss you",
                "body": "Here's 10% off",
            },
            "cited_guidelines": ["07"],
        }
    )


@pytest.fixture
def conn(tmp_path):
    return build_db(tmp_path)


def test_describe_dataset_reports_real_vocabulary(conn):
    out = tools.describe_dataset(_ctx(conn), tools.DescribeDatasetArgs())
    assert out["total_users"] == 10
    assert set(out["categoricals"]["country"]) == {
        "us",
        "in",
        "ng",
        "de",
        "br",
    }  # lower-cased
    assert "voice_agent" in out["features"]
    assert out["lifecycle_stages"] and out["predicate_fields"]


def test_query_segment_returns_grounded_count(conn):
    out = tools.query_segment(_ctx(conn), US_ONLY)
    assert out["count"] == 3
    assert out["total_users"] == 10
    assert out["too_broad"] is False


def test_create_campaign_grounds_size_and_dedupes(conn):
    ctx = _ctx(conn)
    out = tools.create_campaign(ctx, _draft())
    assert out["status"] == "created"
    assert out["segment_size"] == 3
    assert ctx.campaign_id == out["campaign_id"]
    # Second call in the same run does not create a duplicate.
    again = tools.create_campaign(ctx, _draft())
    assert again["status"] == "already_created"
    assert again["campaign_id"] == out["campaign_id"]


def test_create_campaign_rejects_too_broad_segment(conn):
    out = tools.create_campaign(_ctx(conn), _draft(segment=EVERYONE))
    assert out["status"] == "error"
    assert out["error"] == "segment_too_broad"


def test_search_guidelines_returns_cited_docs(conn, recorded_guidelines):
    out = tools.search_guidelines(
        _ctx(conn), tools.SearchGuidelinesArgs(query=recorded_guidelines)
    )
    assert out["results"]
    assert all(r["doc_id"] for r in out["results"])
