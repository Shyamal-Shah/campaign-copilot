from __future__ import annotations

from collections.abc import Callable

import numpy as np
from openai import OpenAI

from app.shared.config import Settings

# An embedder maps a list of texts to a (n, dim) float32 matrix of unit vectors.
Embedder = Callable[[list[str]], np.ndarray]


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.clip(norms, 1e-12, None)


def make_embedder(settings: Settings) -> Embedder | None:
    """Build an embedder bound to the configured endpoint, or ``None`` if embeddings are off."""
    if not settings.embeddings_configured:
        return None

    client = OpenAI(
        base_url=settings.embed_base_url,
        api_key=settings.embed_api_key or "not-needed",  # local servers ignore the key
    )
    model = settings.embed_model

    def embed(texts: list[str]) -> np.ndarray:
        resp = client.embeddings.create(model=model, input=texts)
        vectors = np.array([d.embedding for d in resp.data], dtype=np.float32)
        return _normalize(vectors)

    return embed
