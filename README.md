# Campaign Copilot

An LLM agent that turns a plain-English marketing goal into a ready-to-launch campaign:
it understands the goal, queries the users/events dataset to build a target segment,
grounds itself in the messaging guidelines (RAG), and drafts + idempotently creates a campaign.

## Architecture

## Highlights

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (manages Python, the virtualenv, dependencies, and running).
- An LLM endpoint & an API key for [OpenRouter](https://openrouter.ai) or OpenAI, **or** a local [Ollama](https://ollama.com) or vLLM.
- An embedding endpoint & an API key for OpenAI or local Ollama or vLLM.

uv installs the right Python automatically (pinned in `.python-version`); you don't need it preinstalled.

## Setup

```bash
uv sync                  # create the venv and install dependencies
cp .env.example .env     # then edit .env (see Configuration below)
```

## Configuration

All settings live in `.env` (loaded automatically). The values you must set are the LLM endpoint/key
and the embedding endpoint/key.

| Variable         | What it is                                                          |
| ---------------- | ------------------------------------------------------------------- |
| `LLM_BASE_URL`   | Any OpenAI-compatible chat endpoint                                 |
| `LLM_API_KEY`    | API key for that endpoint (leave empty for local)                   |
| `LLM_MODELS`     | Comma-separated model ids, primary first (later ones are fallbacks) |
| `EMBED_BASE_URL` | Embedding endpoint (openAI or local)                                |
| `EMBED_API_KEY`  | API key for that endpoint (leave empty for local)                   |
| `EMBED_MODEL`    | Embedding model to be used                                          |

**Provider examples** (set `LLM_BASE_URL` / `LLM_MODELS` accordingly):

- **OpenRouter** — `https://openrouter.ai/api/v1`, e.g. `openai/gpt-4o-mini`. (No embeddings API here —
  set `EMBED_*` to OpenAI or Ollama for retrieval.)
- **OpenAI** — `https://api.openai.com/v1`, e.g. `gpt-4o-mini`; embeddings `text-embedding-3-small`.
- **Ollama (local)** — `http://localhost:11434/v1`, e.g. `llama3.1`; embeddings `nomic-embed-text`. No key.

## Run

> At start-up, the server ingests the corpus once inside the FastAPI lifespan. Embeddings are cached to disk keyed by a hash of the corpus + embedding model, so the first run embeds in a single batch call and every later run loads from cache

```bash
uv run uvicorn main:app --reload
```

### Embed Guidelines

> Manually embed the guidelines to pre-warm the cache (e.g. in a Docker build) or re-embed after editing a guideline.

```bash
uv run campaign-ingest
```

Then check it's up:

```bash
curl http://127.0.0.1:8000/health
```

Interactive API docs are at <http://127.0.0.1:8000/docs>.

## Test

```bash
uv run pytest
```

---

## Request Lifecycle

## Design Decisions

## Backend Engineering

## Agent Design

## RAG

The agent grounds its recommendations in the messaging guidelines under [`guidelines/`](guidelines/)
(17 short markdown docs, ~6–7k tokens total) through a `search_guidelines` tool.

**Ingestion — built at startup, cached on disk.** On boot the server ingests the corpus once
(chunk → embed → index) inside the FastAPI lifespan, mirroring how the segment read-models are built.
Embeddings are cached to disk keyed by a hash of the corpus + embedding model, so the first run embeds
in a single batch call and every later run loads from cache with no network. `GET /health` reports
`embeddings_loaded` only once the index is ready — a healthy server is a ready server, and nothing is
lazily embedded on the request hot path. Ingestion is also available as a standalone `campaign-ingest`
command to pre-warm the cache (e.g. in a Docker build) or re-embed after editing a guideline.

**Chunking — one chunk per document.** Each guideline is a single, self-contained topic, so the whole
doc is the retrieval and citation unit (`doc_id` = the file's numeric prefix). Splitting by `##` heading
would fragment the corpus into ~60-word pieces that embed poorly and drop each doc's lead paragraph; at
this size doc-level chunks are both more coherent and easier to cite. `guidelines/README.md` (a table of
contents, not guidance) is excluded.

**Retrieval — hybrid dense + BM25, fused with RRF.** Every query runs both a dense (embedding cosine)
search and a BM25 lexical search, combined with Reciprocal Rank Fusion. BM25 carries the corpus's hard
jargon ("120 characters", "preheader", "deep link") where embeddings can blur an exact constraint; dense
covers paraphrase ("bring users back" → "win-back").

**Grounding.** `search_guidelines` returns each chunk with its `doc_id`, title, and score. The agent
issues several targeted queries per goal (brand voice, the relevant playbook, channel rules, incentives)
and accumulates the results, deduped by `doc_id`. Every guideline the campaign cites must trace back to a
real retrieval result — citations the agent can't ground are rejected (see Agent Design / Evaluation).

**Testing.** Both halves of the baseline — dense (embedding cosine) and BM25 lexical — and their RRF
fusion are tested offline against a small committed fixture of recorded vectors
(`tests/fixtures/guideline_vectors.npz`), so no key or network is needed. Each asserts the expected
guidelines land in the top-k; the dense half also covers a paraphrase ("how often…" → frequency capping)
that lexical matching alone wouldn't.

## API

## Evaluation

## Tradeoffs

## Known Limitations

## Future Improvements
