from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import ScriptedModel, build_db, call, make_app

from app.core.agent.agent import build_agent
from app.core.agent.types import PlannerState
from app.core.observability.trace import RunTrace
from app.features.campaign import service as campaign_service
from app.shared.config import get_settings

from agents import Runner

US_ONLY = {
    "match": "all",
    "predicates": [{"field": "country", "op": "in", "values": ["US"]}],
}


def _create_args() -> dict:
    # Flat create_campaign payload (no segment — it's read from PlannerState; flat message fields).
    return {
        "name": "Winback US",
        "channel": "push",
        "title": "We miss you",
        "body": "Here's 10% off",
        "offer": {"type": "discount", "value": "10%"},
        "rationale": "lapsed US users respond to a small incentive",
        "cited_guidelines": ["07", "13"],
    }


def _created_turns(query: str) -> list[list]:
    """A full plan: size -> retrieve -> create. A successful create_campaign ends the run
    (tool_use_behavior), so there's no separate final-message turn — the outcome is read from
    PlannerState, not a model-emitted CopilotOutcome."""
    return [
        [call("c1", "query_segment", US_ONLY)],
        [call("c2", "search_guidelines", {"query": query})],
        [call("c3", "create_campaign", _create_args())],
    ]


@pytest.mark.asyncio
async def test_runner_loop_creates_grounded_campaign(tmp_path, recorded_guidelines):
    conn = build_db(tmp_path)
    model = ScriptedModel(_created_turns(recorded_guidelines))
    ctx = PlannerState(db=conn, settings=get_settings(), trace=RunTrace())

    await Runner.run(
        build_agent(model, conn, get_settings()), "win back US users", context=ctx, max_turns=8
    )

    # Outcome is grounded in PlannerState (create_campaign set campaign_id), not the model's text.
    assert ctx.campaign_id is not None
    step_names = [s.name for s in ctx.trace.steps]
    assert {
        "query_segment",
        "search_guidelines",
        "create_campaign",
    } <= set(step_names)
    persisted = campaign_service.get_campaign(conn, ctx.campaign_id)
    assert persisted.segment_size == 3  # grounded by a real query, not the model's text


def test_http_run_persists_trace_and_is_idempotent(tmp_path, recorded_guidelines):
    conn = build_db(tmp_path)
    model = ScriptedModel(_created_turns(recorded_guidelines))
    client = TestClient(make_app(conn, agent=build_agent(model, conn, get_settings())))
    headers = {"Idempotency-Key": "run-1"}

    # POST returns 202 immediately; BackgroundTask runs synchronously inside TestClient.
    r = client.post("/copilot/run", json={"goal": "win back US users"}, headers=headers)
    assert r.status_code == 202
    body = r.json()
    assert body["idempotency"]["state"] == "accepted"
    trace_id = body["trace_id"]

    # Poll: by the time TestClient returns from post(), the background task is already done.
    run = client.get(f"/runs/{trace_id}").json()
    assert run["status"] == "created"
    assert run["campaign"]["segment_size"] == 3
    assert any(s["name"] == "create_campaign" for s in run["steps"])

    # Retry with the same key: returns same trace_id, agent NOT re-run, still one campaign.
    calls_before = model.calls
    again = client.post(
        "/copilot/run", json={"goal": "win back US users"}, headers=headers
    )
    assert again.status_code == 200
    assert again.json()["idempotency"]["state"] == "already_exists"
    assert again.json()["trace_id"] == trace_id
    assert (
        model.calls == calls_before
    )  # reservation short-circuited before any model call
    assert len(client.get("/campaigns").json()) == 1


def test_out_of_dsl_goal_declines_cleanly(tmp_path):
    conn = build_db(tmp_path)
    # The agent declines by calling the terminal `finish` tool — not a free-text outcome.
    turns = [
        [
            call(
                "f1",
                "finish",
                {"status": "unsupported", "message": "lookalike segments aren't supported by the DSL"},
            )
        ]
    ]
    client = TestClient(make_app(conn, agent=build_agent(ScriptedModel(turns), conn, get_settings())))

    r = client.post(
        "/copilot/run",
        json={"goal": "users who look like our best customers"},
        headers={"Idempotency-Key": "u-1"},
    )
    assert r.status_code == 202
    run = client.get(f"/runs/{r.json()['trace_id']}").json()
    assert run["status"] == "unsupported"
    assert run.get("campaign_id") is None
    assert len(client.get("/campaigns").json()) == 0  # nothing created


def test_missing_idempotency_key_is_400(tmp_path):
    conn = build_db(tmp_path)
    client = TestClient(make_app(conn, agent=build_agent(ScriptedModel([]), conn, get_settings())))
    assert client.post("/copilot/run", json={"goal": "x"}).status_code == 400


def test_no_llm_configured_is_503(tmp_path):
    client = TestClient(make_app(build_db(tmp_path), agent=None))
    r = client.post(
        "/copilot/run", json={"goal": "x"}, headers={"Idempotency-Key": "n-1"}
    )
    assert r.status_code == 503
