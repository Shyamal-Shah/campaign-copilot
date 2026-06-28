from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import ScriptedModel, build_db, make_app, msg

from agents import Model

from app.core.agent import fallback
from app.core.agent.agent import build_agent
from app.core.llm.client import FallbackModel
from app.core.observability.trace import RunTrace
from app.features.segment.dsl import Country, IsPayer, LifecycleStage
from app.shared.config import get_settings


# --- tiny test models -----------------------------------------------------------------------------
class _BoomModel(Model):
    """A model that always raises — stands in for a down provider."""

    def __init__(self, exc: Exception | None = None):
        self.exc = exc or RuntimeError("provider down")
        self.calls = 0

    async def get_response(self, *a, **k):
        self.calls += 1
        raise self.exc

    def stream_response(self, *a, **k):
        raise NotImplementedError


class _OkModel(Model):
    def __init__(self, value="served"):
        self.value = value
        self.calls = 0

    async def get_response(self, *a, **k):
        self.calls += 1
        return self.value

    def stream_response(self, *a, **k):
        raise NotImplementedError


# --- plan_segment: keyword → DSL (pure) -----------------------------------------------------------
def test_plan_segment_winback_maps_to_lapsed_stages():
    seg = fallback.plan_segment("win back churned users with a discount")
    stage = next(p for p in seg.predicates if isinstance(p, LifecycleStage))
    assert "churned" in stage.values


def test_plan_segment_detects_country_and_payer():
    seg = fallback.plan_segment("re-engage paying US users")
    assert any(isinstance(p, Country) and p.values == ["US"] for p in seg.predicates)
    assert any(isinstance(p, IsPayer) and p.value is True for p in seg.predicates)


def test_plan_segment_unknown_goal_falls_back_to_safe_default():
    seg = fallback.plan_segment("blast everyone right now")
    # Never empty → never the whole base; the default targets re-engageable users.
    assert seg.predicates
    assert any(isinstance(p, LifecycleStage) for p in seg.predicates)


def test_plan_segment_does_not_match_us_inside_words():
    # "users"/"status" must not trip the US country matcher (word-boundary guard).
    seg = fallback.plan_segment("check the status of all users")
    assert not any(isinstance(p, Country) for p in seg.predicates)


# --- run_degraded: grounded, idempotent create with no LLM ----------------------------------------
def test_run_degraded_creates_grounded_campaign(tmp_path):
    conn = build_db(tmp_path)
    trace = RunTrace(goal="win back US users")
    campaign = fallback.run_degraded("win back US users", conn, get_settings(), trace)
    assert campaign is not None
    assert campaign.segment_size > 0  # grounded by a real query, not invented
    assert trace.degraded is True
    assert trace.campaign_id == campaign.campaign_id


def test_run_degraded_returns_none_on_degenerate_base(tmp_path):
    # A single-user base: every candidate segment is either empty or 100% (too broad) → no safe pick.
    one_user = [("only", "2025-01-01", "US", "iOS", "3.4.0", "free")]
    conn = build_db(tmp_path, users=one_user, events=[])
    trace = RunTrace()
    assert fallback.run_degraded("win back users", conn, get_settings(), trace) is None
    assert trace.degraded is False


# --- FallbackModel: rotate to the next model on failure -------------------------------------------
@pytest.mark.asyncio
async def test_fallback_model_rotates_to_secondary_on_error():
    primary, secondary = _BoomModel(), _OkModel("from-secondary")
    seen: list[int] = []
    model = FallbackModel([primary, secondary], on_failover=lambda i, e: seen.append(i))
    out = await model.get_response()
    assert out == "from-secondary"
    assert primary.calls == 1 and secondary.calls == 1
    assert seen == [0]  # the primary (index 0) failed over


@pytest.mark.asyncio
async def test_fallback_model_raises_when_all_models_fail():
    model = FallbackModel([_BoomModel(), _BoomModel(ValueError("also down"))])
    with pytest.raises(ValueError):  # the last model's error surfaces
        await model.get_response()


# --- end-to-end: a failing agent degrades through /copilot/run ------------------------------------
def test_copilot_run_degrades_when_model_fails(tmp_path, recorded_guidelines):
    conn = build_db(tmp_path)
    agent = build_agent(_BoomModel(ConnectionError("down")), conn, get_settings())
    client = TestClient(make_app(conn, agent=agent))

    r = client.post(
        "/copilot/run",
        json={"goal": "win back US users"},
        headers={"Idempotency-Key": "deg-1"},
    )
    assert r.status_code == 202
    run = client.get(f"/runs/{r.json()['trace_id']}").json()
    assert run["status"] == "created"
    assert run["degraded"] is True
    assert run["campaign"]["segment_size"] > 0
    assert len(client.get("/campaigns").json()) == 1  # one grounded campaign, no LLM


def test_run_fails_when_agent_ends_without_a_terminal_tool(tmp_path):
    # The model emits a plain-text "all done!" without calling create_campaign or finish. Nothing
    # was persisted and the run never declined — fail honestly rather than inventing a result.
    conn = build_db(tmp_path)
    turns = [[msg("all done! the campaign is live.")]]
    client = TestClient(make_app(conn, agent=build_agent(ScriptedModel(turns), conn, get_settings())))

    r = client.post(
        "/copilot/run",
        json={"goal": "win back US users"},
        headers={"Idempotency-Key": "claims-1"},
    )
    assert r.status_code == 202
    run = client.get(f"/runs/{r.json()['trace_id']}").json()
    assert run["status"] == "error"
    assert run.get("campaign_id") is None
    assert len(client.get("/campaigns").json()) == 0  # nothing persisted
    assert any(s["name"] == "no_terminal_action" for s in run["steps"])  # cause is queryable


def test_degraded_run_is_idempotent_on_retry(tmp_path):
    conn = build_db(tmp_path)
    agent = build_agent(_BoomModel(), conn, get_settings())
    client = TestClient(make_app(conn, agent=agent))
    headers = {"Idempotency-Key": "deg-2"}

    first = client.post("/copilot/run", json={"goal": "win back US users"}, headers=headers)
    assert first.status_code == 202
    again = client.post("/copilot/run", json={"goal": "win back US users"}, headers=headers)
    # Completed reservation short-circuits; the degraded planner does not run twice.
    assert again.status_code == 200
    assert again.json()["idempotency"]["state"] == "already_exists"
    assert len(client.get("/campaigns").json()) == 1
