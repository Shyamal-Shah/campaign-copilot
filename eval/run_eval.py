from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.core.observability.logging import configure_logging
from app.shared.config import get_settings

from eval import harness, scorecard
from eval.golden_cases import CASES

DEFAULT_REPORT = Path(__file__).resolve().parent / "REPORT.md"


def main() -> int:
    # Quiet the per-step JSON run logs (first configure_logging call wins) so the scorecard is readable.
    configure_logging(level=logging.WARNING)
    settings = get_settings()
    print(
        f"Campaign Copilot eval — {len(CASES)} golden cases (as-of {settings.as_of_date})"
    )

    results = []

    # --- Tier A: always, no network ---
    try:
        conn = harness.build_eval_db(settings)
    except Exception as exc:
        print(
            f"\nFATAL: could not build the eval DB from {settings.source_db_path!r}: {exc}"
        )
        print("Tier A needs the provided dataset at that path. Aborting.")
        return 2
    have_fixture = harness.install_fixture_store(settings)
    if not have_fixture:
        print(
            "note: embeddings fixture missing — Tier-A citation checks will be skipped."
        )
    try:
        for case in CASES:
            results.append(
                harness.run_tier_a(case, conn, settings, have_fixture=have_fixture)
            )
    finally:
        harness.teardown_store()

    # --- Tier B: real LLM, only when configured ---
    if settings.llm_configured and settings.embeddings_configured:
        print("\nrunning Tier B against the configured LLM + embeddings…")
        try:
            results.extend(harness.run_tier_b(CASES, settings))
        except Exception as exc:
            print(f"Tier B skipped after error: {exc!r}")
    else:
        print(
            "\nTier B skipped (set LLM_* and EMBED_* to score against a real model). "
            f"llm_configured={settings.llm_configured} "
            f"embeddings_configured={settings.embeddings_configured}"
        )

    a_passed = scorecard.render(results)

    # Write the Markdown report (path overridable as the first CLI arg) for linking from the README.
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_REPORT
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out.write_text(
        scorecard.render_markdown(
            results, as_of=settings.as_of_date, generated_at=generated_at
        ),
        encoding="utf-8",
    )
    print(f"\nReport written to {out}")

    return 0 if a_passed else 1


if __name__ == "__main__":
    sys.exit(main())
