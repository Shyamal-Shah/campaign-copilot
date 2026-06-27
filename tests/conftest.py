from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from agents import Model, ModelResponse, set_tracing_disabled
from agents.usage import Usage

from app.core.agent.router import router as copilot_router
from app.core.observability.router import router as runs_router
from app.features.campaign.router import router as campaigns_router
from app.features.guidelines import service as guidelines_service
from app.features.guidelines.ingest import load_chunks
from app.features.guidelines.store import GuidelineStore
from app.features.segment.metrics import build_metrics
from app.features.segment.router import router as segments_router
from app.shared.db import connect_app, init_schema

_FIXTURE = Path(__file__).parent / "fixtures" / "guideline_vectors.npz"

set_tracing_disabled(True)

AS_OF = "2026-06-24"

# A tiny base: 3 US users + 7 others, a couple of payers — enough for non-empty, not-too-broad segments.
DEFAULT_USERS = [
    ("u1", "2025-01-01", "US", "iOS", "3.4.0", "free"),
    ("u2", "2025-01-01", "US", "Android", "3.4.0", "pro"),
    ("u3", "2025-01-01", "US", "iOS", "3.4.0", "free"),
    ("u4", "2025-01-01", "IN", "Android", "3.4.0", "free"),
    ("u5", "2025-01-01", "IN", "Android", "3.4.0", "free"),
    ("u6", "2025-01-01", "NG", "Web", "3.4.0", "free"),
    ("u7", "2025-01-01", "NG", "Web", "3.4.0", "pro"),
    ("u8", "2025-01-01", "DE", "iOS", "3.4.0", "free"),
    ("u9", "2025-01-01", "DE", "iOS", "3.4.0", "enterprise"),
    ("u10", "2025-01-01", "BR", "Android", "3.4.0", "free"),
]
DEFAULT_EVENTS = [
    ("e1", "u2", "purchase", "2026-06-20T10:00:00Z", "{}"),
    ("e2", "u7", "purchase", "2026-06-20T10:00:00Z", "{}"),
    (
        "e3",
        "u1",
        "feature_used",
        "2026-06-20T10:00:00Z",
        '{"feature_name": "voice_agent"}',
    ),
]


def synthetic_source(tmp_path, users=DEFAULT_USERS, events=DEFAULT_EVENTS) -> str:
    path = tmp_path / "source.sqlite"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE users (user_id TEXT PRIMARY KEY, signup_date TEXT, country TEXT, "
        "platform TEXT, app_version TEXT, plan TEXT)"
    )
    con.execute(
        "CREATE TABLE events (event_id TEXT PRIMARY KEY, user_id TEXT, event_name TEXT, "
        "timestamp TEXT, properties TEXT)"
    )
    con.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)", users)
    con.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?)", events)
    con.commit()
    con.close()
    return str(path)


def build_db(
    tmp_path, users=DEFAULT_USERS, events=DEFAULT_EVENTS
) -> sqlite3.Connection:
    conn = connect_app(":memory:")
    init_schema(conn)
    build_metrics(conn, synthetic_source(tmp_path, users, events), AS_OF)
    return conn


@pytest.fixture
def recorded_guidelines():
    """Install the offline guidelines store (committed real vectors) and yield a recorded query."""
    chunks = load_chunks("guidelines")
    fx = np.load(_FIXTURE)
    mapping = {c.text: fx["doc_vectors"][i] for i, c in enumerate(chunks)}
    mapping.update(
        {str(q): fx["query_vectors"][i] for i, q in enumerate(fx["query_texts"])}
    )

    def embed(texts):
        return np.stack([mapping[t] for t in texts])

    guidelines_service._store = GuidelineStore(
        chunks, embeddings=fx["doc_vectors"], embedder=embed
    )
    yield str(fx["query_texts"][0])
    guidelines_service._store = None


def make_app(conn: sqlite3.Connection, agent=None) -> FastAPI:
    """A bare app with the routers mounted and state injected — no lifespan, no network."""
    app = FastAPI()
    app.include_router(segments_router)
    app.include_router(campaigns_router)
    app.include_router(copilot_router)
    app.include_router(runs_router)

    @app.exception_handler(RequestValidationError)
    async def _ve(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400, content={"detail": jsonable_encoder(exc.errors())}
        )

    app.state.db = conn
    app.state.agent = agent
    return app


# --- scripted model -------------------------------------------------------------------------------
def msg(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id="m",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


def call(call_id: str, name: str, args: dict) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        type="function_call",
        call_id=call_id,
        name=name,
        arguments=json.dumps(args),
    )


class ScriptedModel(Model):
    """Replays a fixed list of per-turn output items; counts how many times it was asked to respond."""

    def __init__(self, turns: list[list]):
        self.turns = turns
        self.calls = 0

    async def get_response(self, *a, **k) -> ModelResponse:
        out = self.turns[self.calls]
        self.calls += 1
        return ModelResponse(
            output=out,
            usage=Usage(requests=1, input_tokens=20, output_tokens=10, total_tokens=30),
            response_id=None,
            request_id=None,
        )

    def stream_response(self, *a, **k):
        raise NotImplementedError
