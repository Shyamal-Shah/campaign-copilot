# Campaign Copilot

An LLM agent that turns a plain-English marketing goal into a ready-to-launch campaign:
it understands the goal, queries the users/events dataset to build a target segment,
grounds itself in the messaging guidelines (RAG), and drafts + idempotently creates a campaign.

## Architecture

## Highlights

- **Grounded outcomes, never the model's prose.** A campaign's `segment_size` is a real query count and
  the result is derived from a typed `PlannerState` (was a campaign actually persisted?), not from what
  the model claims — a run that *says* it succeeded but never created anything is rejected.
- **Resilience in depth.** An ordered `LLM_MODELS` fallback chain rotates on per-call errors; if the
  whole agent path still fails (every model down, `max_turns`, wall-clock budget), a **deterministic
  degraded planner** still returns a grounded, idempotent campaign with zero LLM.
- **Safe segments by construction.** A typed DSL compiles to **parameterized SQL** (no LLM-written SQL,
  no injection surface), with `empty`/`too_broad` sanity flags and a hard reach cap at create.
- **Hybrid RAG.** Dense (embedding cosine) + BM25 fused with RRF over the guidelines corpus, with
  embeddings cached to disk by a corpus+model hash so later boots need no network.
- **Built for weak/open models.** Flat tool payloads, no forced JSON output type, typed correctable tool
  errors, and the dataset's real vocabulary injected into the system prompt so predicates stay valid.
- **Observable & offline-testable.** Every run persists a step-level `RunTrace` (tokens, latency, cost)
  retrievable at `GET /runs/{trace_id}`; the full orchestration is tested with no API key by driving the
  real SDK loop with a scripted model over the real tools.

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


Then check it's up:

```bash
curl http://127.0.0.1:8000/health
```

Interactive API docs are at <http://127.0.0.1:8000/docs>.


## Embed Guidelines

> Manually embed the guidelines to pre-warm the cache (e.g. in a Docker build) or re-embed after editing a guideline.

```bash
uv run campaign-ingest
```

## Test

```bash
uv run pytest
```

## Eval

```bash
uv run campaign-eval          # Tier A always (deterministic, no network); Tier B when LLM_*/EMBED_* are set
```

Prints a per-case + aggregate scorecard, writes [eval/REPORT.md](eval/REPORT.md), and exits non-zero on
any Tier-A failure (so it doubles as a CI gate). See [Evaluation](#evaluation) for the approach and
metrics.

---

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

### Campaigns & idempotency

- **Required `Idempotency-Key` header, reserved before the agent runs.** The agent path is
  non-deterministic - a client that times out right after creation and retries the same goal can get
  differently-worded copy on the rerun. Hashing the _generated content_ would then produce a new key and a
  silent duplicate, so the key is tied to the request and the reservation is taken before any LLM call; a
  retry returns the stored response without re-running the model.
- **A two-state reservation** The key row goes `in_progress` → `completed`, and the `key`
  primary key is the entire correctness argument - concurrent first-time requests race on the `INSERT`,
  exactly one wins and does the work, the losers read the existing row (409 while in flight, the cached
  response once done). No application-level locking.
- **Grounded, hard-blocked create.** `create_campaign` re-runs the segment query so the stored
  `segment_size` is a real count, and a segment over `MAX_SEGMENT_REACH` is refused at create - the
  preview's soft `too_broad` flag becomes a hard block where it actually matters.
- **Validators as guardrails.** doc-03 push limits (title ≤50, body ≤120) and an HTTPS + domain-allowlist
  link check are Pydantic validators, so a non-compliant draft fails validation and the agent gets one
  corrective round-trip instead of a bad campaign reaching the DB.

## Backend Engineering

### Data layer

- When the app DB is first created, we populate it from the source in a separate `app.sqlite`: a
lowercased, indexed **`users`** profile copy, plus two tables aggregated from the event log -
**`user_metrics`** (one row per user - last-activity timestamps, `days_since_*`, 30-day activity counts,
`purchase_count`/`is_payer`, `total_events`, `lifecycle_stage`) and **`user_features`** (a normalized
user↔feature set).

### Segments

- A segment is a flat `match` (`all`/`any`) over typed leaf predicates: profile (`country`/`plan`/
`platform`), behavioral (`days_since_*`, `purchase_count`, `is_payer`, `lifecycle_stage`), feature
adoption (`used`/`not_used_feature`), and windowed frequency (`event_count`). 
- A pure compiler turns the
definition into `(from, where, params)` with every value bound - profile predicates join `users`,
behavioral ones read `user_metrics`, feature ones are `EXISTS` over `user_features`, and `event_count`
is a windowed subquery over `events`. `POST /segments/preview` runs it and returns the count,
`pct_of_base`, a small `users` sample, and the `empty`/`too_broad` flags - all with no LLM. Value
matching is case-insensitive.

### Campaigns & idempotency

- `POST /copilot/run` (agent) use an idempotency path: `reserve(key)`
does an `INSERT OR IGNORE` and either returns `reserved` (the caller does the work, then `complete()`
caches the response JSON) or reads the existing row (`completed` → return the cached response;
`in_progress` → 409). 
- A handled failure calls `release()` to drop the in-progress row so the client can
retry; a missing header is a 400 (the required `Header(...)` surfaces through the same validation→400
handler). For `POST /copilot/run` the reservation is taken synchronously before returning the 202; the
background task runs the agent and calls `complete()` when done. 
- A completed key short-circuits to
200 with the cached `trace_id` — the agent never re-runs. `create_campaign` is a plain insert that
grounds `segment_size` through the segment compiler and enforces the reach cap, so idempotency stays
a single cross-cutting reservation wrapping either a direct create or a whole agent run rather than
per-write logic.
-  Messages persist as a discriminated-union JSON blob with `channel`/`image_url`
derived from the message so they can never disagree with it.

## Agent Design

The agent runs on the **OpenAI Agents SDK** over any OpenAI-compatible chat endpoint. It _plans its own
steps_ - there is no hardcoded tool order - and is driven through `POST /copilot/run`.

- **Fire-and-forget entry point.** `POST /copilot/run` reserves the idempotency key, persists a
placeholder `in_progress` trace, then returns `202 {trace_id}` immediately. The agent runs in a
FastAPI `BackgroundTask`; clients poll `GET /runs/{trace_id}` for status and the created campaign.
A completed key short-circuits to `200 {trace_id, already_exists}` with no task spawned.

- **Planning loop, outcome derived from state (not prose).** The agent decides which tools to call and
when; the run is bounded by `max_turns` (SDK loop cap), the per-call `LLM_TIMEOUT_S` on the HTTP client,
_and_ a wall-clock `RUN_BUDGET_S` backstop (`asyncio.wait_for`) so a genuinely hung run on a slow/open
model still terminates into the fallback path. There is deliberately no forced json response:
a response format makes weaker OpenAI-compatible models skip tool-calling and fabricate a final answer. Instead `tool_use_behavior` ends the loop the moment a \_real
terminal effect* lands - a campaign was persisted, or the `finish` tool was called - and the outcome is
read from a typed `PlannerState`, never the model's text. The `finish` tool is how the agent **declines
cleanly**: `unsupported` for a goal the segment DSL can't express (lookalike / ML segments, cross-user
similarity) or `needs_clarification` with one specific question - instead of looping to `max_turns` or
inventing a campaign.

- **Tool Execution Layer.** Every tool is registered as a `ToolSpec` and wrapped by a single
**ToolExecutor** middleware pipeline - argument validation → timeout (run off the event loop) → bounded
retries → a recorded trace step - so the four tool impls stay tiny and uniform and the cross-cutting
concerns live in one place. A failure becomes a _typed error result the model can read and recover from_
(an over-broad segment, an invalid field), never a raw exception or stack trace leaking to the LLM. These
tool-level timeouts/retries are a different failure domain from the model-level retries in the LLM client.

- **Four tools.** `query_segment` compiles the DSL to SQL and returns a real count + sample;
`search_guidelines` is the RAG tool (below); `create_campaign` assembles the channel message from flat
fields, validates it, and idempotently persists the draft; `finish` is the terminal decline. The
dataset's _real_ vocabulary - countries, platforms, plans, lifecycle stages, features, event names,
predicate fields - is injected into the **system prompt** (built at agent construction by querying the
read-models), so the model builds valid predicates without a separate lookup tool. `create_campaign`
takes deliberately **flat fields** (channel + `title`/`body`/… rather than a discriminated-union message)
and reads the audience from the last `query_segment` result on `PlannerState`, so a weaker model never
has to re-type the recursive segment DSL.

- **Grounding without trusting the model's prose.** A created campaign's numbers are not whatever the model
retyped: `create_campaign` uses the exact segment `query_segment` sized (from `PlannerState`) and
`campaign_service` _re-runs that query itself_, so `segment_size` is a real count and an over-broad
segment is hard-blocked at create. The response and the cached idempotency record read the `campaign_id`
from the run context (set by the tool), not from the model's text - so a run that _claims_ success but
never created anything is rejected rather than echoed back. A caller-supplied campaign `name` is honored
verbatim and a `channel_hint` is woven into the agent input so the channel and copy stay consistent.

- **Resilience: fallback chain → degraded planner.** Two env-configured layers stand between a flaky
provider and a failed run. (1) `LLM_MODELS` is an ordered chain - a `FallbackModel` rotates to the next
model on any per-call error, all sharing one client; a single entry means no wrapper. (2) When the whole
agent path still fails - every model down, `max_turns`, the wall-clock budget, or a loop that ends
without persisting a campaign - the router falls back to a **deterministic degraded planner**
(`app.core.agent.fallback`) that builds a grounded campaign with _zero LLM_: a keyword→DSL segment sized
by a real query, a guideline-compliant templated message, created through the same idempotent path, with
the run marked `degraded=true`. The run only truly fails (`error`, idempotency key released) if even the
degraded planner finds no usable segment for the base.

- **Run context, not chat memory.** Tools share a typed `PlannerState` (DB handle, settings, the run trace,
the last-sized segment, the created `campaign_id`, the `finish` outcome, and caller hints) threaded via
the SDK's run context and _never sent to the LLM_. It is the single source of run truth: the router
derives the final status from it, `create_campaign` reads the grounded segment from it, and a successful
create or `finish` recorded on it is what ends the loop.

- **Observability.** Each tool call appends a step (name, status, latency, a compact summary) to a per-run
`RunTrace`; the run records token usage and an estimated cost, persists to a `runs` table, and is
retrievable at `GET /runs/{trace_id}` - the "debug a bad run" deliverable. The SDK's trace export to
OpenAI's backend is disabled (`set_tracing_disabled`); the application maintains its own trace locally.

- **Idempotent and offline-testable.** The idempotency key is reserved synchronously before the 202 is
returned, so a client that retries with the same key gets the cached `trace_id` and polls `GET /runs/…`
rather than triggering a second agent run. The whole orchestration is tested with no LLM key by
driving the real SDK `Runner` loop with a scripted model over the real tools - the happy path, trace
persistence, idempotent replay, the clean `unsupported` decline, a run that ends without a terminal tool
(honest `error`), and the resilience path: a model that always raises degrades to a grounded campaign,
and that degraded run is itself idempotent on retry.

## RAG

The agent grounds its recommendations in the messaging guidelines under [`guidelines/`](guidelines/)
(17 short markdown docs, ~6–7k tokens total) through a `search_guidelines` tool.

- **Ingestion - built at startup, cached on disk.** On boot the server ingests the corpus once
(chunk → embed → index) inside the FastAPI lifespan, mirroring how the segment read-models are built.
Embeddings are cached to disk keyed by a hash of the corpus + embedding model, so the first run embeds
in a single batch call and every later run loads from cache with no network. `GET /health` reports
`embeddings_loaded` only once the index is ready - a healthy server is a ready server, and nothing is
lazily embedded on the request hot path. Ingestion is also available as a standalone `campaign-ingest`
command to pre-warm the cache (e.g. in a Docker build) or re-embed after editing a guideline.

- **Chunking - one chunk per document.** Each guideline is a single, self-contained topic, so the whole
doc is the retrieval and citation unit (`doc_id` = the file's numeric prefix). Splitting by `##` heading
would fragment the corpus into ~60-word pieces that embed poorly and drop each doc's lead paragraph; at
this size doc-level chunks are both more coherent and easier to cite. `guidelines/README.md` (a table of
contents, not guidance) is excluded.

- **Retrieval - hybrid dense + BM25, fused with RRF.** Every query runs both a dense (embedding cosine)
search and a BM25 lexical search, combined with Reciprocal Rank Fusion. BM25 carries the corpus's hard
jargon ("120 characters", "preheader", "deep link") where embeddings can blur an exact constraint; dense
covers paraphrase ("bring users back" → "win-back").

- **Grounding.** `search_guidelines` returns each chunk's `doc_id`, title, body, and score. The agent
issue several targeted queries per goal (channel rules, copy limits, incentives, the relevant lifecycle
playbook), apply what it retrieved, and cite only the `doc_id`s it actually used. Grounding currently
relies on the prompt plus mechanical create-time checks (real segment size, reach cap); programmatically
rejecting citations the agent cannot trace back to a retrieval result is a future enhancement.

- **Testing.** Both halves of the baseline - dense (embedding cosine) and BM25 lexical - and their RRF
fusion are tested offline against a small committed fixture of recorded vectors
(`tests/fixtures/guideline_vectors.npz`), so no key or network is needed. Each asserts the expected
guidelines land in the top-k; the dense half also covers a paraphrase ("how often…" → frequency capping)
that lexical matching alone wouldn't.

## Evaluation

A small harness in [`eval/`](eval/) measures whether the agent produces *reasonable* segments and
campaigns — the instinct to measure quality in a non-deterministic system, not a full eval framework.
The latest run is committed at **[eval/REPORT.md](eval/REPORT.md)**; regenerate with:

```bash
uv run campaign-eval          # Tier A always; Tier B when LLM_*/EMBED_* are set
```

**Two tiers.** *Tier A* is deterministic and needs no network: it drives the **real** compiler,
hybrid retrieval (via the committed embeddings fixture), and agent loop over the **real dataset**, with
a scripted model standing in for the LLM — so grounding/idempotency/observability are checked on every
run and the command doubles as a CI gate (non-zero exit on any Tier-A failure). *Tier B* runs only when
a provider is configured: it sends each plain-English goal to the **actual agent** and scores the
outcome with range/shape assertions (never a magic count).

**Seven metrics**, per [`eval/golden_cases.py`](eval/golden_cases.py) and read mostly straight from the
persisted `RunTrace`: five pass/fail checks — **grounding** (a campaign's size/citations come from real
tool effects, not the model's prose), **segment** (real-data count in the expected range *and* shape),
**citations** (recall@k: expected `doc_id`s ⊆ retrieved), **dedupe** (a retried key never
double-creates), **declines** (an out-of-DSL "lookalike" goal refuses cleanly with no campaign) — plus
three distributions: **latency**, **tool count** (no runaway loop), and **token usage**. Counts are
anchored to the real data (payers≈809, active≤14d≈3262) but asserted as **ranges**, so a
different-but-valid segment still passes.

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

### Campaigns & idempotency

- **Header key over content hash.** Retry-safe across the non-deterministic agent path and simpler (one
  `status` column, no canonicalization), at the cost of making clients send a key - which we require
  (400 if absent) rather than treat as best-effort.

### Agent

- **202 fire-and-forget over synchronous response.** Frees the HTTP connection for slow/open models
  (no timeout on the caller side), but adds a polling round-trip and means a server restart kills
  in-flight background tasks, leaving the key stuck `in_progress`.
- **Outcome derived from `PlannerState`, not a typed `output_type`.** A forced JSON response_format makes
  weaker OpenAI-compatible models skip tool-calling and fabricate an answer, so we drop it and read the
  result from real tool effects instead - more robust across providers, at the cost of one extra moving
  part (`tool_use_behavior`) to end the loop on a terminal effect.
- **Simple ordered fallback chain over a stateful circuit breaker.** `FallbackModel` rotates per call and
  is trivially correct, but it has no open-state / cooldown - a persistently-down primary is retried on
  every run rather than being short-circuited until it recovers.
- **Deterministic keyword degraded planner over a second LLM.** When models are down the fallback is pure
  keyword→DSL matching: explainable, zero-dependency, and always grounded, but coarser than the agent (a
  small set of lifecycle/country/payer cues and templated copy) rather than a nuanced segment.
- **Bounded model-level retries (`LLM_MAX_RETRIES`, default 3).** The SDK client retries transient
  errors before `FallbackModel` rotates; generous for flaky open endpoints, but it spends part of the
  per-call timeout budget, so the two are tuned together.

## Known Limitations

### Data layer

- **A changed source isn't picked up automatically.** The build runs only when the app DB is empty, so
  refreshing after `data.sqlite` changes means deleting `app.sqlite`.
- **Recency is relative to a fixed as-of date** (2026-06-24), so the read-model is a static snapshot,
  not "now".
- **Activity-count windows are hardcoded to 30 days** (`app_open_count_30d`, `session_count_30d`);
  arbitrary windows aren't precomputed .
- **One SQLite connection is shared across the request threadpool** (`check_same_thread=False`) - fine
  for a single process, not safe for multi-worker / multi-node writes.

### Segments

- **Flat only** - no nested boolean groups / `NOT`-groups, and no temporal "event A then B" funnels yet.
- **Only lifecycle-stage values are validated** against a vocabulary; unknown country / feature / event
  names just yield an empty match rather than a clear error.
- **`event_count` is recomputed per query** over `events` (no materialized counts for arbitrary windows).

### Campaigns & idempotency

- **A server restart or crash leaves the key stuck `in_progress`.** For the synchronous `POST /campaigns`
  path a handled failure calls `release()`; for `POST /copilot/run` the agent runs as a background
  task that dies on server shutdown with no cleanup hook. A TTL reaper to auto-release stale
  reservations is not yet implemented.
- **Non-`created` outcomes are cached too.** `unsupported` are stored under the
  key, so a retry returns the same decline - a transiently-flaky decline isn't re-attempted.

### Agent

- **Fallback chain rotates but has no circuit breaker** - a persistently-down primary model is re-tried
  on every run (no open-state / cooldown to skip it until it recovers).
- **The degraded planner is coarse** - keyword→DSL over a small cue set with templated copy; it
  guarantees a grounded, idempotent campaign when the LLM is down, not an equally nuanced one.
- **No SSE streaming** - clients get status updates by polling `GET /runs/{trace_id}`, not a live event
  stream; `Runner.run_streamed` + SSE is a future upgrade.
- **Background task has no shutdown hook** - a server restart kills the in-flight agent with no
  cleanup, leaving the idempotency key stuck `in_progress` (see Campaigns & idempotency).
- **Per-tool timeout bounds the await, not in-flight sync work** - the impl runs in a thread, so a wedged
  synchronous call keeps running; fine for the fast in-process tools here.

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

### Campaigns & idempotency

- **TTL reaper for stale reservations** so a crashed run's key auto-frees, and move the idempotency +
  campaigns tables to a shared store (Postgres / Redis) for multi-worker idempotency.

### Agent

- **Per-model circuit breaker** on top of the existing fallback chain - track failures per model and
  open (skip) a down one with a cooldown, instead of retrying it on every run.
- **Programmatic guardrails:** input scope/prompt-injection checks and output validation (URL
  allowlist, content safety) as deterministic pre/post steps, plus rejecting citations the agent can't
  trace back to a retrieval result, rather than trusting the prompt for those.
- **Streaming (SSE)** via `Runner.run_streamed` for live tool / segment / draft events.

