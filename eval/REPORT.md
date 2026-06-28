# Campaign Copilot — Eval Scorecard

_Generated 2026-06-28 11:11 UTC · dataset as-of 2026-06-24 · 8/8 cases passed · Tier A ✅ OK_

Regenerate with `uv run campaign-eval` (Tier A always; Tier B when `LLM_*`/`EMBED_*` are set).

Checks: **grounding** (size/citations from real tool effects, not model prose) · **segment** (real-data count in range + shape) · **citations** (recall@k: expected `doc_id`s ⊆ retrieved) · **dedupe** (a retried key never double-creates) · **declines** (out-of-DSL goal refused cleanly). Measures: latency_ms · tools · tokens.

### Tier A — deterministic, no network

| case | result | grounding | segment | citations | dedupe | declines | latency_ms | tools | tokens | notes |
|---|---|---|---|---|---|---|---|---|---|---|
| winback_us_push | ✅ PASS | ✅ | ✅ | ✅ | ✅ | – | 5.8 | 3 | 90 | count=678 (range 400-900); retrieved=['07', '13', '17']; status=created |
| onboarding_new_inapp | ✅ PASS | ✅ | ✅ | ✅ | ✅ | – | 5.1 | 3 | 90 | count=509 (range 300-900); retrieved=['06', '17', '02']; status=created |
| active_payers_push | ✅ PASS | ✅ | ✅ | ✅ | ✅ | – | 5.4 | 3 | 90 | count=809 (range 700-900); retrieved=['03', '12', '04']; status=created |
| lookalike_unsupported | ✅ PASS | – | – | – | – | ✅ | 1.1 | 1 | 30 | status=unsupported |

**Aggregate:** grounding 3/3 · segment 3/3 · citations 3/3 · dedupe 3/3 · declines 1/1 · latency_ms p50=5.4/p95=5.8 · tools p50=3.0/p95=3.0 · tokens p50=90.0/p95=90.0

### Tier B — real LLM

| case | result | grounding | segment | citations | dedupe | declines | latency_ms | tools | tokens | notes |
|---|---|---|---|---|---|---|---|---|---|---|
| winback_us_push | ✅ PASS | ✅ | ✅ | ✅ | ✅ | – | 8644.6 | 5 | 14806 | size=467 channel=push cited=['02', '03', '07', '13'] expect⊇['07', '13'] hit=['07', '13'] |
| onboarding_new_inapp | ✅ PASS | ✅ | ✅ | ✅ | ✅ | – | 7532.6 | 6 | 17201 | size=509 channel=in_app cited=['01', '06', '10'] expect⊇['06'] hit=['06'] |
| active_payers_push | ✅ PASS | ✅ | ✅ | ✅ | ✅ | – | 5829.7 | 6 | 16937 | size=809 channel=push cited=['01', '03', '04'] expect⊇['03'] hit=['03'] |
| lookalike_unsupported | ✅ PASS | – | – | – | – | ✅ | 2117.2 | 1 | 2325 | status=unsupported |

**Aggregate:** grounding 3/3 · segment 3/3 · citations 3/3 · dedupe 3/3 · declines 1/1 · latency_ms p50=7532.6/p95=8644.6 · tools p50=6.0/p95=6.0 · tokens p50=16937.0/p95=17201.0
