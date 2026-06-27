from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.features.guidelines import service
from app.shared.config import get_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """On startup: ingest the guidelines (cache hit = instant; miss = one batch embed + cache)."""
    service.init_store(settings)
    yield


app = FastAPI(
    title="Campaign Copilot",
    version="0.1.0",
    summary="LLM agent that turns a plain-English marketing goal into a ready-to-launch campaign.",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    """Liveness probe; reports retrieval readiness and configuration presence."""
    return {
        "status": "ok",
        "as_of_date": settings.as_of_date,
        "embeddings_loaded": service.store_ready(),
        "llm_configured": settings.llm_configured,
        "embeddings_configured": settings.embeddings_configured,
        "model_chain": settings.model_chain,
    }
