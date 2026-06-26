# Campaign Copilot

An LLM agent that turns a plain-English marketing goal into a ready-to-launch campaign:
it understands the goal, queries the users/events dataset to build a target segment,
grounds itself in the messaging guidelines (RAG), and drafts + idempotently creates a campaign.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (manages Python, the virtualenv, dependencies, and running).
- An LLM endpoint — an API key for [OpenRouter](https://openrouter.ai) or OpenAI, **or** a local [Ollama](https://ollama.com) or vLLM.

uv installs the right Python automatically (pinned in `.python-version`); you don't need it preinstalled.

## Setup

```bash
uv sync                  # create the venv and install dependencies
cp .env.example .env     # then edit .env (see Configuration below)
```

## Configuration

All settings live in `.env` (loaded automatically). The only values you normally need to set are the
LLM endpoint and key.

| Variable       | What it is                                                          |
| -------------- | ------------------------------------------------------------------- |
| `LLM_BASE_URL` | Any OpenAI-compatible chat endpoint                                 |
| `LLM_API_KEY`  | API key for that endpoint (leave empty for local)                   |
| `LLM_MODELS`   | Comma-separated model ids, primary first (later ones are fallbacks) |
| `EMBED_*`      | Optional embedding endpoint for semantic retrieval (unset → offline BM25 fallback) |

Retrieval (RAG over the guidelines) uses embeddings when `EMBED_*` is set; otherwise it falls back to
an in-process **BM25** lexical index, so it works fully offline with no embedding provider.

**Provider examples** (set `LLM_BASE_URL` / `LLM_MODELS` accordingly):

- **OpenRouter** — `https://openrouter.ai/api/v1`, e.g. `openai/gpt-4o-mini`. (No embeddings here — for
  semantic retrieval set `EMBED_*` to OpenAI or Ollama, or leave it unset to use the BM25 fallback.)
- **OpenAI** — `https://api.openai.com/v1`, e.g. `gpt-4o-mini`; embeddings `text-embedding-3-small`.
- **Ollama (local)** — `http://localhost:11434/v1`, e.g. `llama3.1`; embeddings `nomic-embed-text`. No key.

## Run

```bash
uv run uvicorn main:app --reload
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
