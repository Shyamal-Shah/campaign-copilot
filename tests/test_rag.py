from pathlib import Path

import numpy as np
import pytest

from app.features.guidelines.ingest import ingest, load_chunks
from app.features.guidelines.store import GuidelineStore, rank_desc, rrf_fuse
from app.shared.config import Settings

GUIDELINES = "guidelines"
_FIXTURE = Path(__file__).parent / "fixtures" / "guideline_vectors.npz"


def _doc_ids(results):
    return [r.doc_id for r in results]


def _recorded():
    """Return (embedder, chunks, doc_vectors) backed by the committed real vectors (offline)."""
    chunks = load_chunks(GUIDELINES)
    fx = np.load(_FIXTURE)
    assert [str(x) for x in fx["doc_ids"]] == [c.doc_id for c in chunks]  # fixture matches corpus
    mapping = {c.text: fx["doc_vectors"][i] for i, c in enumerate(chunks)}
    mapping.update({str(q): fx["query_vectors"][i] for i, q in enumerate(fx["query_texts"])})

    def embed(texts):
        return np.stack([mapping[t] for t in texts])

    return embed, chunks, fx["doc_vectors"]


def _recorded_store():
    embed, chunks, doc_vectors = _recorded()
    return GuidelineStore(chunks, embeddings=doc_vectors, embedder=embed)


def test_corpus_is_doc_level_and_excludes_readme():
    chunks = load_chunks(GUIDELINES)
    assert len(chunks) == 17  # the 17 numbered guidelines, README excluded
    assert all(c.doc_id.isdigit() and c.title for c in chunks)


def test_rrf_fusion_is_pure():
    # dense ranks [A,B,C]=[0,1,2]; bm25 ranks [C,A,B]=[2,0,1] → fused order A,C,B
    fused = rrf_fuse([[0, 1, 2], [2, 0, 1]])
    assert [idx for idx, _ in fused] == [0, 2, 1]


def test_bm25_recall():
    """The lexical (BM25) half of the baseline surfaces the expected docs."""
    store = GuidelineStore(load_chunks(GUIDELINES), embeddings=None)
    assert {"07", "13"} <= set(_doc_ids(store.search("win back churned users", k=5)))
    assert "03" in _doc_ids(store.search("push notification character limit", k=5))


def test_dense_recall():
    """The semantic (dense embedding) half of the baseline surfaces the expected docs."""
    embed, chunks, doc_vectors = _recorded()

    def dense(query, k=5):
        scores = (doc_vectors @ embed([query])[0]).tolist()
        return [chunks[i].doc_id for i in rank_desc(scores)[:k]]

    assert {"07", "13"} <= set(dense("win back churned users with a discount"))
    assert "03" in dense("push notification character limit")
    assert "06" in dense("onboarding new signups")
    # the dense half catches a paraphrase that lexical matching alone wouldn't
    assert "12" in dense("how often should we message users")


def test_hybrid_recall_with_recorded_vectors():
    """Our baseline — the real dense + BM25 + RRF retrieval — surfaces the expected docs."""
    store = _recorded_store()
    assert store.hybrid
    assert {"07", "13"} <= set(_doc_ids(store.search("win back churned users with a discount", k=5)))
    assert "03" in _doc_ids(store.search("push notification character limit", k=5))
    assert "06" in _doc_ids(store.search("onboarding new signups", k=5))
    # a paraphrase the lexical signal alone wouldn't surface — the dense half pulls in frequency capping
    assert "12" in _doc_ids(store.search("how often should we message users", k=5))


def test_ingest_is_idempotent_and_cache_backed(tmp_path):
    """First ingest embeds once and writes the cache; a second loads it (no re-embed)."""
    embed, _, _ = _recorded()
    settings = Settings(cache_dir=str(tmp_path), guidelines_dir=GUIDELINES)
    calls = {"n": 0}

    def counting(texts):
        calls["n"] += 1
        return embed(texts)

    store1 = ingest(settings, embedder=counting)
    assert calls["n"] == 1
    assert store1.embeddings is not None and store1.embeddings.shape[0] == 17

    store2 = ingest(settings, embedder=counting)  # cache hit
    assert calls["n"] == 1  # not re-embedded
    assert store2.embeddings is not None
    assert store2.embeddings.shape == store1.embeddings.shape


def test_ingest_fails_fast_without_embeddings(tmp_path):
    """No embedding provider configured and no cache → ingestion refuses to build a half-working RAG."""
    settings = Settings(
        cache_dir=str(tmp_path), guidelines_dir=GUIDELINES, embed_base_url="", embed_api_key=""
    )
    with pytest.raises(RuntimeError, match="Embeddings are required"):
        ingest(settings)
