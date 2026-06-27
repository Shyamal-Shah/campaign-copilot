from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.features.guidelines import service
from app.features.segment.metrics import count_user_metrics, ensure_metrics
from app.features.segment.router import router as segments_router
from app.shared.config import get_settings
from app.shared.db import attach_source, connect_app, init_schema

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Behavioural read-models from the events log
    conn = connect_app(settings.app_db_path)
    init_schema(conn)
    ensure_metrics(conn, settings.source_db_path, settings.as_of_date)
    # Attach the source read-only so segment queries can reach `users` / `events`.
    attach_source(conn, settings.source_db_path)
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
app.include_router(segments_router)


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return 400 Bad Request (not FastAPI's default 422) for invalid request bodies."""
    return JSONResponse(status_code=400, content={"detail": jsonable_encoder(exc.errors())})


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
