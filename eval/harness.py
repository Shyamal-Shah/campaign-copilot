from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import numpy as np
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from openai.types.responses import ResponseFunctionToolCall

from agents import Model, ModelResponse, set_tracing_disabled
from agents.usage import Usage

from app.core.agent.agent import build_agent
from app.core.agent.router import router as copilot_router
from app.core.observability.router import router as runs_router
from app.features.campaign.router import router as campaigns_router
from app.features.guidelines import service as guidelines_service
from app.features.guidelines.ingest import load_chunks
from app.features.guidelines.service import search_guidelines as rag_search
from app.features.guidelines.store import GuidelineStore
from app.features.segment.metrics import build_metrics
from app.features.segment.router import router as segments_router
from app.features.segment.service import preview_segment
from app.shared.config import Settings
from app.shared.db import attach_source, connect_app, init_schema

from eval.golden_cases import GoldenCase
from eval.scorecard import CaseResult

set_tracing_disabled(True)

_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "guideline_vectors.npz"
)


# --- scripted model (minimal, mirrors the one used in the test suite) ------------------------------
def _call(call_id: str, name: str, args: dict) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        type="function_call", call_id=call_id, name=name, arguments=json.dumps(args)
    )


class ScriptedModel(Model):
    """Replays a fixed list of per-turn output items over the real tools."""

    def __init__(self, turns: list[list]):
        self.turns = turns
        self.calls = 0

    async def get_response(self, *a, **k) -> ModelResponse:
        out = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        return ModelResponse(
            output=out,
            usage=Usage(requests=1, input_tokens=20, output_tokens=10, total_tokens=30),
            response_id=None,
            request_id=None,
        )

    def stream_response(self, *a, **k):
        raise NotImplementedError


# --- environment ----------------------------------------------------------------------------------
def build_eval_db(settings: Settings):
    """Build a fresh app DB from the real source dataset (file-based so the source can be ATTACHed)."""
    path = os.path.join(tempfile.mkdtemp(prefix="cc-eval-"), "eval.sqlite")
    conn = connect_app(path)
    init_schema(conn)
    build_metrics(conn, settings.source_db_path, settings.as_of_date)
    attach_source(conn, settings.source_db_path)
    return conn


def install_fixture_store(settings: Settings) -> bool:
    """Install the offline guidelines store (committed real vectors) so retrieval runs with no network.

    Returns False if the fixture is missing (Tier-A citation checks are then skipped).
    """
    if not _FIXTURE.exists():
        return False
    chunks = load_chunks(settings.guidelines_dir)
    fx = np.load(_FIXTURE, allow_pickle=True)
    mapping = {c.text: fx["doc_vectors"][i] for i, c in enumerate(chunks)}
    mapping.update(
        {str(q): fx["query_vectors"][i] for i, q in enumerate(fx["query_texts"])}
    )

    def embed(texts):
        return np.stack([mapping[t] for t in texts])

    guidelines_service._store = GuidelineStore(
        chunks, embeddings=fx["doc_vectors"], embedder=embed
    )
    return True


def teardown_store() -> None:
    guidelines_service._store = None


def make_eval_app(conn, agent) -> FastAPI:
    """A bare app with the routers mounted and state injected — no lifespan, no network."""
    app = FastAPI()
    for r in (segments_router, campaigns_router, copilot_router, runs_router):
        app.include_router(r)

    @app.exception_handler(RequestValidationError)
    async def _ve(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400, content={"detail": jsonable_encoder(exc.errors())}
        )

    app.state.db = conn
    app.state.agent = agent
    return app


# --- shared scoring helpers -----------------------------------------------------------------------
def _create_args(case: GoldenCase, cited: list[str]) -> dict:
    args = {
        "name": case.name.replace("_", " ").title(),
        "channel": case.channel,
        "title": case.title,
        "body": case.body,
        "cited_guidelines": cited,
    }
    if case.cta_text:
        args["cta_text"] = case.cta_text
    if case.offer:
        args["offer"] = case.offer
    return args


def _tool_count(run: dict) -> int:
    return sum(1 for s in run.get("steps", []) if s.get("kind") == "tool")


def _measures(run: dict) -> dict[str, float]:
    return {
        "latency_ms": float(run.get("total_ms") or 0.0),
        "tools": _tool_count(run),
        "tokens": int(run.get("total_tokens") or 0),
    }


def _poll(client: TestClient, goal: str, channel_hint: str | None, key: str) -> dict:
    """POST /copilot/run (background task runs synchronously in TestClient) and read the trace."""
    body = {"goal": goal}
    if channel_hint:
        body["channel_hint"] = channel_hint
    resp = client.post("/copilot/run", json=body, headers={"Idempotency-Key": key})
    trace_id = resp.json()["trace_id"]
    return resp.json(), client.get(f"/runs/{trace_id}").json()


# --- Tier A: deterministic, scripted model over real tools ----------------------------------------
def run_tier_a(
    case: GoldenCase, conn, settings: Settings, *, have_fixture: bool
) -> CaseResult:
    try:
        if case.kind == "decline":
            return _run_tier_a_decline(case, conn, settings)
        return _run_tier_a_create(case, conn, settings, have_fixture=have_fixture)
    except Exception as exc:  # a harness error shouldn't crash the whole scorecard
        return CaseResult(name=case.name, tier="A", kind=case.kind, error=repr(exc))


def _run_tier_a_create(
    case: GoldenCase, conn, settings: Settings, *, have_fixture: bool
) -> CaseResult:
    res = CaseResult(name=case.name, tier="A", kind="create")

    # 1. Direct, no-agent checks: the segment compiles to a sane real count, retrieval has recall.
    preview = preview_segment(
        conn, case.segment, settings.as_of_date, settings.max_segment_reach
    )
    res.checks["segment"] = (
        case.min_count <= preview.count <= case.max_count
        and not preview.empty
        and not preview.too_broad
    )
    retrieved = [r.doc_id for r in rag_search(case.rag_query)] if have_fixture else []
    if have_fixture:
        res.checks["citations"] = set(case.expect_docs) <= set(retrieved)

    # 2. Drive the real agent loop with a scripted plan over the real tools.
    cited = list(case.expect_docs) if case.expect_docs else retrieved[:2]
    model = ScriptedModel(
        [
            [_call("c1", "query_segment", case.segment.model_dump())],
            [_call("c2", "search_guidelines", {"query": case.rag_query})],
            [_call("c3", "create_campaign", _create_args(case, cited))],
        ]
    )
    client = TestClient(make_eval_app(conn, build_agent(model, conn, settings)))
    key = f"eval-A-{case.name}"
    n_before = len(client.get("/campaigns").json())
    _, run = _poll(client, case.goal, case.channel, key)
    campaign = run.get("campaign")

    # Grounding: size + citations come from real tool effects, not the model's prose.
    res.checks["grounding"] = bool(
        campaign
        and campaign["segment_size"] == preview.count
        and (not have_fixture or set(campaign["cited_guidelines"]) <= set(retrieved))
    )

    # Dedupe: a retry with the same key must not create a second campaign.
    again, _ = _poll(client, case.goal, case.channel, key)
    n_after = len(client.get("/campaigns").json())
    res.checks["dedupe"] = (
        again["idempotency"]["state"] == "already_exists" and n_after == n_before + 1
    )

    res.measures = _measures(run)
    res.notes = (
        f"count={preview.count} (range {case.min_count}-{case.max_count}); "
        f"retrieved={retrieved}; status={run.get('status')}"
    )
    return res


def _run_tier_a_decline(case: GoldenCase, conn, settings: Settings) -> CaseResult:
    res = CaseResult(name=case.name, tier="A", kind="decline")
    model = ScriptedModel(
        [
            [
                _call(
                    "c1",
                    "finish",
                    {
                        "status": case.decline_status,
                        "message": "not expressible in the DSL",
                    },
                )
            ]
        ]
    )
    client = TestClient(make_eval_app(conn, build_agent(model, conn, settings)))
    n_before = len(client.get("/campaigns").json())
    _, run = _poll(client, case.goal, None, f"eval-A-{case.name}")
    n_after = len(client.get("/campaigns").json())
    res.checks["declines"] = (
        run.get("status") == case.decline_status
        and run.get("campaign_id") is None
        and n_after == n_before
        and _tool_count(run) <= 3  # bounded — no runaway loop
    )
    res.measures = _measures(run)
    res.notes = f"status={run.get('status')}"
    return res


# --- Tier B: real LLM, lenient range/shape scoring ------------------------------------------------
def run_tier_b(cases: list[GoldenCase], settings: Settings) -> list[CaseResult]:
    from app.core.llm.client import make_chat_model

    conn = build_eval_db(settings)
    guidelines_service.init_store(settings)  # real embeddings index
    model = make_chat_model(settings)
    if model is None:
        return []
    client = TestClient(make_eval_app(conn, build_agent(model, conn, settings)))
    corpus = {c.doc_id for c in load_chunks(settings.guidelines_dir)}
    return [_run_tier_b_case(case, client, settings, corpus) for case in cases]


def _run_tier_b_case(
    case: GoldenCase, client: TestClient, settings: Settings, corpus: set[str]
) -> CaseResult:
    res = CaseResult(name=case.name, tier="B", kind=case.kind)
    try:
        key = f"eval-B-{case.name}"
        n_before = len(client.get("/campaigns").json())
        _, run = _poll(client, case.goal, None, key)
        res.measures = _measures(run)

        if case.kind == "decline":
            res.checks["declines"] = (
                run.get("status") in ("unsupported", "needs_clarification")
                and run.get("campaign_id") is None
            )
            res.notes = f"status={run.get('status')}"
            return res

        campaign = run.get("campaign")
        # Grounding: a real campaign with a real, non-trivial size and only real cited doc_ids.
        res.checks["grounding"] = bool(
            campaign
            and campaign["segment_size"] > 0
            and set(campaign["cited_guidelines"]) <= corpus
        )
        # Segment sanity: non-empty and not the whole base (can't pin an exact shape from NL).
        res.checks["segment"] = bool(
            campaign
            and 0 < campaign["segment_size"] < 5000 * settings.max_segment_reach
        )
        # Citations: the agent cited at least one guideline, ideally one we expected.
        cited = set(campaign["cited_guidelines"]) if campaign else set()
        res.checks["citations"] = bool(cited)
        # Dedupe: retry the same key.
        again, _ = _poll(client, case.goal, None, key)
        n_after = len(client.get("/campaigns").json())
        res.checks["dedupe"] = (
            again["idempotency"]["state"] == "already_exists"
            and n_after == n_before + 1
        )
        hit = sorted(cited & set(case.expect_docs))
        res.notes = (
            f"size={campaign['segment_size'] if campaign else '-'} "
            f"channel={campaign['channel'] if campaign else '-'} "
            f"cited={sorted(cited)} expect⊇{list(case.expect_docs)} hit={hit}"
        )
    except Exception as exc:
        res.error = repr(exc)
    return res
