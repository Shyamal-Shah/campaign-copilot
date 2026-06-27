from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.features.guidelines import service
from app.features.segment.metrics import count_user_metrics, ensure_metrics
from app.shared.config import get_settings
from app.shared.db import connect_app, init_schema

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Behavioural read-models from the events log
    conn = connect_app(settings.app_db_path)
    init_schema(conn)
    ensure_metrics(conn, settings.source_db_path, settings.as_of_date)
    app.state.db = conn
    # 2. Guidelines retrieval index (requires an embedding provider; cache-backed, fails fast).
    service.init_store(settings)
    try:
        yield
    finally:
        conn.close()


app = FastAPI(
    title="Campaign Copilot",
    version="0.1.0",
    summary="LLM agent that turns a plain-English marketing goal into a ready-to-launch campaign.",
    lifespan=lifespan,
)


@app.get("/health")
def health(request: Request) -> dict:
    """Liveness probe; reports read-model readiness and configuration presence."""
    db = getattr(request.app.state, "db", None)
    return {
        "status": "ok",
        "as_of_date": settings.as_of_date,
        "user_metrics_rows": count_user_metrics(db) if db is not None else None,
        "embeddings_loaded": service.store_ready(),
        "llm_configured": settings.llm_configured,
        "embeddings_configured": settings.embeddings_configured,
        "model_chain": settings.model_chain,
    }
