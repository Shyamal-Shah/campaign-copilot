from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.llm.embeddings import Embedder, make_embedder
from app.shared.config import Settings, get_settings

# Bump when the chunking strategy changes, so cached vectors built the old way are invalidated.
CHUNK_VERSION = "doc-v1"

_DOC_FILE = re.compile(r"^(\d+)-.*\.md$")
_H1 = re.compile(r"^#\s+(.*)$", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    """One guideline document."""

    doc_id: str  # filename numeric prefix, e.g. "07"
    title: str  # the document's H1
    text: str  # full document body (used for both embedding and BM25)


def load_chunks(guidelines_dir: str) -> list[Chunk]:
    """Read every ``NN-*.md`` guideline (skipping README) into one chunk each, ordered by doc_id."""
    chunks: list[Chunk] = []
    for path in Path(guidelines_dir).iterdir():
        match = _DOC_FILE.match(path.name)
        if not match:
            continue  # skips README.md and anything not numbered
        text = path.read_text(encoding="utf-8").strip()
        h1 = _H1.search(text)
        title = h1.group(1).strip() if h1 else path.stem
        chunks.append(Chunk(doc_id=match.group(1), title=title, text=text))
    chunks.sort(key=lambda c: c.doc_id)
    return chunks


def _fingerprint(chunks: list[Chunk], embed_model: str) -> str:
    """Stable hash of corpus content + embedding model + chunk strategy."""
    hasher = hashlib.sha256()
    hasher.update(CHUNK_VERSION.encode())
    hasher.update(embed_model.encode())
    for chunk in chunks:
        hasher.update(chunk.doc_id.encode())
        hasher.update(chunk.text.encode("utf-8"))
    return hasher.hexdigest()[:16]


def _cache_path(settings: Settings, chunks: list[Chunk]) -> Path:
    return (
        Path(settings.cache_dir)
        / f"guidelines.{_fingerprint(chunks, settings.embed_model)}.npy"
    )


def ingest(settings: Settings | None = None, embedder: Embedder | None = None):
    """Build the guideline store (idempotent, cache-backed). Fail-fast if embeddings are unavailable.

    Returns a ``GuidelineStore`` (imported lazily to avoid a circular import).
    """
    settings = settings or get_settings()
    resolved = embedder if embedder is not None else make_embedder(settings)
    if resolved is None:
        raise RuntimeError(
            "Embeddings are required for retrieval but none are configured. "
            "Set EMBED_BASE_URL / EMBED_API_KEY / EMBED_MODEL (see README)."
        )

    chunks = load_chunks(settings.guidelines_dir)
    cache_path = _cache_path(settings, chunks)
    if cache_path.exists():
        vectors = np.load(cache_path)  # boot from cache; the provider is not contacted
    else:
        vectors = resolved(
            [c.text for c in chunks]
        )  # one batch call; raises if the provider is down
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache_path, vectors)

    from app.features.guidelines.store import GuidelineStore

    return GuidelineStore(chunks, embeddings=vectors, embedder=resolved)


def main() -> None:
    """Console entry point (``campaign-ingest``): build/warm the embedding cache."""
    store = ingest()
    assert store.embeddings is not None  # ingest() always embeds or raises, so this holds
    print(
        f"campaign-ingest: embedded {len(store.chunks)} guideline docs "
        f"(dim={store.embeddings.shape[1]}); cache ready."
    )


if __name__ == "__main__":
    main()
