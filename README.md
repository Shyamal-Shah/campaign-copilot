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

- **OpenRouter** - `https://openrouter.ai/api/v1`, e.g. `openai/gpt-4o-mini`. (No embeddings API here -
  set `EMBED_*` to OpenAI or Ollama for retrieval.)
- **OpenAI** - `https://api.openai.com/v1`, e.g. `gpt-4o-mini`; embeddings `text-embedding-3-small`.
- **Ollama (local)** - `http://localhost:11434/v1`, e.g. `llama3.1`; embeddings `nomic-embed-text`. No key.

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

### Data layer

- **Precomputed read-models over per-request aggregation.** Behavior is rolled up into
  `user_metrics` / `user_features` once, when the app DB is first created, so segment queries are indexed
  column lookups rather than scans over the 236k-row event log.
- **Read-only source, disposable `app.sqlite`.** Derived state never touches the provided dataset, so the
  input stays pristine and the app DB is disposable - delete it and it regenerates on the next boot,
  nothing to migrate.
- **Normalized `user_features` table.** One row per (user, feature) makes adoption filters index-friendly
  `EXISTS` checks rather than `LIKE` over a packed string.
- **Values sanitized in the read copy for index-friendly case-insensitivity.** The source `users` is
  copied into the app DB with categoricals (country / platform / plan) and feature names lowercased and
  indexed.

### Segments

- **Structured DSL compiled to parameterized SQL, never LLM-written SQL.** The model fills in a typed
  segment definition and we compile it; there is no SQL-injection surface, the compiler is a pure
  unit-testable function, and the definition canonicalizes cleanly for idempotency later.
- **Typed, validated predicates.** Each predicate is a discriminated union on its `field` with allowed
  operators and value shapes; an invalid field/op/value is rejected with `400`, not silently turned into
  a wrong query.
- **Size sanity flags (`empty` / `too_broad`).** A preview reports whether a segment is empty or covers
  an implausibly large share of the base (> `MAX_SEGMENT_REACH`) - the cheapest guard against a
  mis-defined "blast everyone" segment (it becomes a hard block at create time later).
- **Source attached read-only for raw events.** `event_count` predicates run a windowed subquery over the
  source `events` (236k rows, attached read-only - not worth copying); profile and behavioral predicates
  hit our local, indexed read copies.

## Backend Engineering

### Data layer

When the app DB is first created, we populate it from the source in a separate `app.sqlite`: a
lowercased, indexed **`users`** profile copy, plus two tables aggregated from the event log -
**`user_metrics`** (one row per user - last-activity timestamps, `days_since_*`, 30-day activity counts,
`purchase_count`/`is_payer`, `total_events`, `lifecycle_stage`) and **`user_features`** (a normalized
user↔feature set).

### Segments

A segment is a flat `match` (`all`/`any`) over typed leaf predicates: profile (`country`/`plan`/
`platform`), behavioral (`days_since_*`, `purchase_count`, `is_payer`, `lifecycle_stage`), feature
adoption (`used`/`not_used_feature`), and windowed frequency (`event_count`). A pure compiler turns the
definition into `(from, where, params)` with every value bound - profile predicates join `users`,
behavioral ones read `user_metrics`, feature ones are `EXISTS` over `user_features`, and `event_count`
is a windowed subquery over `events`. `POST /segments/preview` runs it and returns the count,
`pct_of_base`, a small `users` sample, and the `empty`/`too_broad` flags - all with no LLM. Value
matching is case-insensitive.

## Agent Design

## RAG

The agent grounds its recommendations in the messaging guidelines under [`guidelines/`](guidelines/)
(17 short markdown docs, ~6–7k tokens total) through a `search_guidelines` tool.

**Ingestion - built at startup, cached on disk.** On boot the server ingests the corpus once
(chunk → embed → index) inside the FastAPI lifespan, mirroring how the segment read-models are built.
Embeddings are cached to disk keyed by a hash of the corpus + embedding model, so the first run embeds
in a single batch call and every later run loads from cache with no network. `GET /health` reports
`embeddings_loaded` only once the index is ready - a healthy server is a ready server, and nothing is
lazily embedded on the request hot path. Ingestion is also available as a standalone `campaign-ingest`
command to pre-warm the cache (e.g. in a Docker build) or re-embed after editing a guideline.

**Chunking - one chunk per document.** Each guideline is a single, self-contained topic, so the whole
doc is the retrieval and citation unit (`doc_id` = the file's numeric prefix). Splitting by `##` heading
would fragment the corpus into ~60-word pieces that embed poorly and drop each doc's lead paragraph; at
this size doc-level chunks are both more coherent and easier to cite. `guidelines/README.md` (a table of
contents, not guidance) is excluded.

**Retrieval - hybrid dense + BM25, fused with RRF.** Every query runs both a dense (embedding cosine)
search and a BM25 lexical search, combined with Reciprocal Rank Fusion. BM25 carries the corpus's hard
jargon ("120 characters", "preheader", "deep link") where embeddings can blur an exact constraint; dense
covers paraphrase ("bring users back" → "win-back").

**Grounding.** `search_guidelines` returns each chunk with its `doc_id`, title, and score. The agent
issues several targeted queries per goal (brand voice, the relevant playbook, channel rules, incentives)
and accumulates the results, deduped by `doc_id`. Every guideline the campaign cites must trace back to a
real retrieval result - citations the agent can't ground are rejected (see Agent Design / Evaluation).

**Testing.** Both halves of the baseline - dense (embedding cosine) and BM25 lexical - and their RRF
fusion are tested offline against a small committed fixture of recorded vectors
(`tests/fixtures/guideline_vectors.npz`), so no key or network is needed. Each asserts the expected
guidelines land in the top-k; the dense half also covers a paraphrase ("how often…" → frequency capping)
that lexical matching alone wouldn't.

## API

## Evaluation

## Tradeoffs

### Data layer

- **Precompute over compute-on-read.** Fast, indexed segment queries, at the cost of a derived copy that
  has to be built and can drift from the source - acceptable because the dataset is fixed.
- **Single-pass full build over incremental refresh.** Simpler and obviously correct, but the cost scales
  with the whole event log - fine at 236k rows, not for a large live one.

### Segments

- **Closed DSL over raw SQL.** Safe and unit-testable, but it can only express the predicates we model -
  a deliberate boundary (arbitrary SQL is out of scope).
- **Flat predicates for now.** `match: all|any` covers most goals; arbitrary boolean nesting and temporal
  funnels are deferred upgrades.

## Known Limitations

### Data layer

- **A changed source isn't picked up automatically.** The build runs only when the app DB is empty, so
  refreshing after `data.sqlite` changes means deleting `app.sqlite`.
- **Recency is relative to a fixed as-of date** (2026-06-24), so the read-model is a static snapshot,
  not "now" as per assignment.
- **Activity-count windows are hardcoded to 30 days** (`app_open_count_30d`, `session_count_30d`);
  arbitrary windows aren't precomputed .
- **One SQLite connection is shared across the request threadpool** (`check_same_thread=False`) - fine
  for a single process, not safe for multi-worker / multi-node writes.

### Segments

- **Flat only** - no nested boolean groups / `NOT`-groups, and no temporal "event A then B" funnels yet.
- **Only lifecycle-stage values are validated** against a vocabulary; unknown country / feature / event
  names just yield an empty match rather than a clear error.
- **`event_count` is recomputed per query** over `events` (no materialized counts for arbitrary windows).

## Future Improvements

### Data layer

- **Incremental refresh instead of build-once.** For a large, continuously-growing event log, refresh
  `user_metrics` / `user_features` incrementally - a scheduled job or change-data-capture updating only
  what changed - rather than computing from the full log.
- **Source fingerprint to auto-rebuild.** Hash the source (size + mtime, plus the as-of date) so the
  read-models rebuild automatically when the dataset changes, instead of needing a manual reset.

### Segments

- **Arbitrary boolean trees + funnel predicates** - `(A or B) and not C`, and temporal sequences like
  "added to cart then not purchased within 2h".
- **Validate values against the live dataset vocabulary** (countries / features / event names) so a bad
  input fails with a clear error instead of a silently-empty segment.
