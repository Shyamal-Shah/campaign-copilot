from __future__ import annotations

from app.features.guidelines.ingest import ingest
from app.features.guidelines.store import GuidelineStore, Retrieved
from app.shared.config import Settings, get_settings

_store: GuidelineStore | None = None


def init_store(settings: Settings | None = None) -> GuidelineStore:
    """Build the store at startup (idempotent, cache-backed). Fail-fast if misconfigured."""
    global _store
    _store = ingest(settings or get_settings())
    return _store


def store_ready() -> bool:
    """True once the store has been built (i.e. retrieval is ready to serve)."""
    return _store is not None


def get_store() -> GuidelineStore:
    """Return the store, building it on first use if startup hasn't already."""
    return _store if _store is not None else init_store()


def search_guidelines(query: str, k: int | None = None) -> list[Retrieved]:
    """Retrieve the top-k guideline documents for a query (hybrid dense + BM25)."""
    settings = get_settings()
    return get_store().search(query, k=k or settings.retrieval_top_k)
