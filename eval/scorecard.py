from __future__ import annotations

from dataclasses import dataclass, field

# Column order for the boolean checks; only those present on a case are rendered for it.
CHECK_COLUMNS = ["grounding", "segment", "citations", "dedupe", "declines"]
MEASURE_COLUMNS = ["latency_ms", "tools", "tokens"]


@dataclass
class CaseResult:
    name: str
    tier: str  # "A" | "B"
    kind: str  # "create" | "decline"
    checks: dict[str, bool] = field(default_factory=dict)
    measures: dict[str, float] = field(default_factory=dict)
    notes: str = ""
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(self.checks.values())


def _fmt_check(v: bool | None) -> str:
    return "  -  " if v is None else (" pass" if v else " FAIL")


def _pctile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    i = min(len(s) - 1, int(round(q * (len(s) - 1))))
    return s[i]


def render(results: list[CaseResult]) -> bool:
    """Print the scorecard. Returns True if every Tier-A case passed (the harness's exit gate)."""
    for tier in ("A", "B"):
        tier_results = [r for r in results if r.tier == tier]
        if not tier_results:
            continue
        label = (
            "Tier A (deterministic, no network)" if tier == "A" else "Tier B (real LLM)"
        )
        print(f"\n=== {label} ===")
        header = f"{'case':24} {'result':7} " + " ".join(
            f"{c:>10}" for c in CHECK_COLUMNS
        )
        header += " " + " ".join(f"{m:>11}" for m in MEASURE_COLUMNS)
        print(header)
        print("-" * len(header))
        for r in tier_results:
            if r.error:
                print(f"{r.name:24} {'ERROR':7} {r.error[:60]}")
                continue
            checks = " ".join(
                f"{_fmt_check(r.checks.get(c)):>10}" for c in CHECK_COLUMNS
            )
            measures = " ".join(
                (
                    f"{r.measures.get(m, 0):>11.1f}"
                    if m == "latency_ms"
                    else f"{int(r.measures.get(m, 0)):>11}"
                )
                for m in MEASURE_COLUMNS
            )
            verdict = "PASS" if r.passed else "FAIL"
            print(f"{r.name:24} {verdict:7} {checks} {measures}")
            if r.notes:
                print(f"{'':24} └─ {r.notes}")

        # Aggregate.
        print("-" * len(header))
        for c in CHECK_COLUMNS:
            graded = [r for r in tier_results if c in r.checks and r.error is None]
            if graded:
                ok = sum(1 for r in graded if r.checks[c])
                print(f"  {c:18} {ok}/{len(graded)} passed")
        for m in MEASURE_COLUMNS:
            vals = [r.measures[m] for r in tier_results if m in r.measures]
            if vals:
                print(
                    f"  {m:18} p50={_pctile(vals, 0.5):.1f}  "
                    f"p95={_pctile(vals, 0.95):.1f}  max={max(vals):.1f}"
                )

    a_results = [r for r in results if r.tier == "A"]
    a_passed = all(r.passed for r in a_results)
    total_pass = sum(1 for r in results if r.passed)
    print(
        f"\nSummary: {total_pass}/{len(results)} cases passed "
        f"(Tier A {'OK' if a_passed else 'FAILED'})."
    )
    return a_passed


# --- markdown report (for committing / linking from the README) -----------------------------------
def _md_check(v: bool | None) -> str:
    return "–" if v is None else ("✅" if v else "❌")


def _md_tier(results: list[CaseResult], tier: str, label: str) -> list[str]:
    tier_results = [r for r in results if r.tier == tier]
    if not tier_results:
        return []
    lines = [f"### {label}", ""]
    head = ["case", "result", *CHECK_COLUMNS, *MEASURE_COLUMNS, "notes"]
    lines.append("| " + " | ".join(head) + " |")
    lines.append("|" + "|".join(["---"] * len(head)) + "|")
    for r in tier_results:
        if r.error:
            lines.append(
                f"| {r.name} | ⚠️ ERROR | "
                + " | ".join([""] * (len(head) - 3))
                + f" | {r.error} |"
            )
            continue
        checks = [_md_check(r.checks.get(c)) for c in CHECK_COLUMNS]
        measures = [
            f"{r.measures.get('latency_ms', 0):.1f}",
            str(int(r.measures.get("tools", 0))),
            str(int(r.measures.get("tokens", 0))),
        ]
        verdict = "✅ PASS" if r.passed else "❌ FAIL"
        lines.append(
            "| " + " | ".join([r.name, verdict, *checks, *measures, r.notes]) + " |"
        )
    # Aggregate line.
    aggs = []
    for c in CHECK_COLUMNS:
        graded = [r for r in tier_results if c in r.checks and r.error is None]
        if graded:
            aggs.append(f"{c} {sum(1 for r in graded if r.checks[c])}/{len(graded)}")
    for m in MEASURE_COLUMNS:
        vals = [r.measures[m] for r in tier_results if m in r.measures]
        if vals:
            aggs.append(
                f"{m} p50={_pctile(vals, 0.5):.1f}/p95={_pctile(vals, 0.95):.1f}"
            )
    lines += ["", "**Aggregate:** " + " · ".join(aggs), ""]
    return lines


def render_markdown(results: list[CaseResult], *, as_of: str, generated_at: str) -> str:
    """Render the scorecard as a Markdown document for committing / linking from the README."""
    a_passed = all(r.passed for r in results if r.tier == "A")
    total_pass = sum(1 for r in results if r.passed)
    tier_b_ran = any(r.tier == "B" for r in results)
    lines = [
        "# Campaign Copilot — Eval Scorecard",
        "",
        f"_Generated {generated_at} · dataset as-of {as_of} · "
        f"{total_pass}/{len(results)} cases passed · "
        f"Tier A {'✅ OK' if a_passed else '❌ FAILED'}_",
        "",
        "Regenerate with `uv run campaign-eval` (Tier A always; Tier B when `LLM_*`/`EMBED_*` are set).",
        "",
        "Checks: **grounding** (size/citations from real tool effects, not model prose) · "
        "**segment** (real-data count in range + shape) · **citations** (recall@k: expected "
        "`doc_id`s ⊆ retrieved) · **dedupe** (a retried key never double-creates) · "
        "**declines** (out-of-DSL goal refused cleanly). Measures: latency_ms · tools · tokens.",
        "",
    ]
    lines += _md_tier(results, "A", "Tier A — deterministic, no network")
    if tier_b_ran:
        lines += _md_tier(results, "B", "Tier B — real LLM")
    else:
        lines += [
            "### Tier B — real LLM",
            "",
            "_Not run in this report (no `LLM_*`/`EMBED_*` configured). Tier B sends each goal to the "
            "real agent and scores the outcome with lenient range/shape assertions._",
            "",
        ]
    return "\n".join(lines)
