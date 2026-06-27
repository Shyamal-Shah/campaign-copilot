from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

import numpy as np

from app.core.llm.embeddings import Embedder
from app.features.guidelines.ingest import Chunk

RRF_K = 60

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class BM25:
    """Okapi BM25 over a small in-memory corpus."""

    def __init__(
        self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75
    ):
        self.k1, self.b = k1, b
        self.doc_len = [len(d) for d in corpus_tokens]
        self.n = len(corpus_tokens)
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.tf = [Counter(d) for d in corpus_tokens]
        df: Counter[str] = Counter()
        for doc in corpus_tokens:
            df.update(set(doc))
        self.idf = {
            term: math.log(1 + (self.n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def scores(self, query: str) -> list[float]:
        terms = tokenize(query)
        out = [0.0] * self.n
        for i in range(self.n):
            tf, dl = self.tf[i], self.doc_len[i]
            denom_norm = (
                self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                if self.avgdl
                else self.k1
            )
            score = 0.0
            for term in terms:
                freq = tf.get(term, 0)
                if freq:
                    score += (
                        self.idf.get(term, 0.0)
                        * (freq * (self.k1 + 1))
                        / (freq + denom_norm)
                    )
            out[i] = score
        return out


def rank_desc(scores: list[float]) -> list[int]:
    """Indices ordered by score, highest first (ties broken by index for determinism)."""
    return sorted(range(len(scores)), key=lambda i: (-scores[i], i))


def rrf_fuse(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion: score(d) = Σ 1/(k + rank). Pure function of the input rankings."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(
            ranking
        ):  # rank 0-based → contribute 1/(k+1) for the top item
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))


@dataclass
class Retrieved:
    doc_id: str
    title: str
    score: float
    text: str


class GuidelineStore:
    """Holds the corpus + indexes and answers ranked retrieval queries."""

    def __init__(
        self,
        chunks: list[Chunk],
        embeddings: np.ndarray | None = None,
        embedder: Embedder | None = None,
    ):
        self.chunks = chunks
        self.embeddings = embeddings  # (n, dim) unit vectors, or None
        self.embedder = embedder
        self._bm25 = BM25([tokenize(c.text) for c in chunks])

    @property
    def hybrid(self) -> bool:
        return self.embeddings is not None and self.embedder is not None

    def search(self, query: str, k: int = 4) -> list[Retrieved]:
        bm25_scores = self._bm25.scores(query)

        embeddings, embedder = self.embeddings, self.embedder
        if embeddings is not None and embedder is not None:
            query_vec = embedder([query])[0]
            dense_scores = (embeddings @ query_vec).tolist()
            fused = rrf_fuse([rank_desc(dense_scores), rank_desc(bm25_scores)])
            order = [idx for idx, _ in fused]
            scores = dict(fused)
        else:
            order = rank_desc(bm25_scores)
            scores = {i: bm25_scores[i] for i in order}

        results = []
        for idx in order[:k]:
            c = self.chunks[idx]
            results.append(Retrieved(c.doc_id, c.title, float(scores[idx]), c.text))
        return results
