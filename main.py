from __future__ import annotations

from fastapi import FastAPI

from app.shared.config import get_settings

settings = get_settings()

app = FastAPI(
    title="Campaign Copilot",
    version="0.1.0",
    summary="LLM agent that turns a plain-English marketing goal into a ready-to-launch campaign.",
)


@app.get("/health")
def health() -> dict:
    """Liveness probe; reports basic configuration presence."""
    return {
        "status": "ok",
        "as_of_date": settings.as_of_date,
        "llm_configured": settings.llm_configured,
        "embeddings_configured": settings.embeddings_configured,
        "model_chain": settings.model_chain,
    }
