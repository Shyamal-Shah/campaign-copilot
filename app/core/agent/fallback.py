from __future__ import annotations

import re
import sqlite3

from app.core.observability.trace import RunTrace
from app.features.campaign import service as campaign_service
from app.features.campaign.models import CampaignDraft, Offer, PushMessage
from app.features.guidelines import service as guidelines_service
from app.features.segment.dsl import (
    Country,
    IsPayer,
    LifecycleStage,
    SegmentDefinition,
)
from app.features.segment.service import preview_segment
from app.shared.config import Settings

# Free-text intent cues. The first lifecycle group that matches wins (checked in this order).
_WINBACK_CUES = (
    "win back",
    "winback",
    "win-back",
    "churn",
    "lapsed",
    "lapsing",
    "dormant",
    "inactive",
    "re-engage",
    "reengage",
    "re engage",
    "come back",
    "comeback",
    "abandon",
)
_NEW_CUES = (
    "new user",
    "newly",
    "onboard",
    "just signed",
    "recently signed",
    "first week",
    "sign up",
    "sign-up",
    "signup",
)
_ACTIVE_CUES = ("active", "engaged", "power user", "loyal", "frequent")
_PAYER_CUES = (
    "payer",
    "paying",
    "premium",
    "paid",
    "subscriber",
    "subscription",
    "purchase",
)

# Country name/code → the dataset's stored value. Matched on word boundaries so "us" doesn't fire
# inside "status"/"focus" and "uk" doesn't fire inside "lukewarm".
_COUNTRY_MAP = {
    "us": "US",
    "u.s.": "US",
    "usa": "US",
    "united states": "US",
    "america": "US",
    "india": "IN",
    "indian": "IN",
    "nigeria": "NG",
    "nigerian": "NG",
    "germany": "DE",
    "german": "DE",
    "indonesia": "ID",
    "indonesian": "ID",
    "uk": "UK",
    "united kingdom": "UK",
    "britain": "UK",
    "england": "UK",
    "brazil": "BR",
    "brazilian": "BR",
}


def _detect_countries(goal: str) -> list[str]:
    found: list[str] = []
    for name, code in _COUNTRY_MAP.items():
        if re.search(rf"\b{re.escape(name)}\b", goal) and code not in found:
            found.append(code)
    return found


def plan_segment(goal: str) -> SegmentDefinition:
    """Best-effort keyword→DSL mapping. Pure (no DB); always yields ≥1 predicate.

    Never returns an empty predicate list, so the segment is never "the entire base"; the caller
    still grounds the real count and only creates if it's non-empty and within the reach cap.
    """
    g = goal.lower()
    predicates: list = []

    if any(c in g for c in _WINBACK_CUES):
        predicates.append(
            LifecycleStage(op="in", values=["lapsing", "dormant", "churned"])
        )
    elif any(c in g for c in _NEW_CUES):
        predicates.append(LifecycleStage(op="in", values=["new"]))
    elif any(c in g for c in _ACTIVE_CUES):
        predicates.append(LifecycleStage(op="in", values=["active"]))

    if any(c in g for c in _PAYER_CUES):
        predicates.append(IsPayer(value=True))

    countries = _detect_countries(g)
    if countries:
        predicates.append(Country(op="in", values=countries))

    if not predicates:
        # No recognizable intent → target re-engageable users (a safe, non-trivial default).
        predicates.append(LifecycleStage(op="in", values=["lapsing", "dormant"]))

    return SegmentDefinition(match="all", predicates=predicates)


def _usable_segment(
    conn: sqlite3.Connection, settings: Settings, goal: str
) -> SegmentDefinition | None:
    """Return the first candidate whose real count is non-empty and within the reach cap.

    Tries the keyword segment first, then progressively broader lifecycle defaults. Returns
    ``None`` only on a degenerate base where nothing is usable (then the caller errors the run).
    """
    candidates = [
        plan_segment(goal),
        SegmentDefinition(predicates=[LifecycleStage(op="in", values=["churned"])]),
        SegmentDefinition(
            predicates=[LifecycleStage(op="in", values=["dormant", "churned"])]
        ),
        SegmentDefinition(
            predicates=[
                LifecycleStage(op="in", values=["lapsing", "dormant", "churned"])
            ]
        ),
    ]
    for seg in candidates:
        r = preview_segment(conn, seg, settings.as_of_date, settings.max_segment_reach)
        if not r.empty and not r.too_broad:
            return seg
    return None


def _build_message(goal: str) -> tuple[PushMessage, Offer | None]:
    """A guideline-compliant push (title ≤50, body ≤120), keyed off the goal's intent."""
    g = goal.lower()
    if any(c in g for c in _WINBACK_CUES):
        return (
            PushMessage(
                title="We miss you \U0001f44b",
                body="Come back and pick up right where you left off.",
            ),
            Offer(
                type="discount",
                value="10%",
                expiry_days=7,
                eligibility_note="re-engagement incentive (degraded-planner default)",
            ),
        )
    if any(c in g for c in _NEW_CUES):
        return (
            PushMessage(
                title="Welcome aboard! \U0001f389",
                body="Here's a quick tip to get the most out of your first week.",
            ),
            None,
        )
    return (
        PushMessage(
            title="Something new for you", body="Open the app to see what's waiting."
        ),
        None,
    )


def run_degraded(
    goal: str,
    conn: sqlite3.Connection,
    settings: Settings,
    trace: RunTrace,
    *,
    name: str | None = None,
) -> campaign_service.Campaign | None:
    """Create a grounded campaign with no LLM, or ``None`` if no usable segment exists.

    Same idempotent create path as the agent; sets ``trace.degraded`` and ``trace.campaign_id``.
    Citations are best-effort: if retrieval is up (it needs no chat LLM) the campaign cites real
    guideline doc_ids; if it's down too, it proceeds without them.
    """
    trace.add_step(
        "note",
        "degraded_planner",
        "ok",
        summary="LLM unavailable — building a deterministic campaign",
    )

    segment = _usable_segment(conn, settings, goal)
    if segment is None:
        trace.add_step(
            "note",
            "degraded_planner",
            "error",
            summary="no usable segment could be built for this base",
        )
        return None

    cited: list[str] = []
    try:
        if guidelines_service.store_ready():
            cited = [r.doc_id for r in guidelines_service.search_guidelines(goal)]
    except Exception:
        cited = []  # retrieval down too — proceed without citations

    message, offer = _build_message(goal)
    draft = CampaignDraft(
        name=name or "Re-engagement campaign (auto)",
        goal=goal,
        segment=segment,
        message=message,
        offer=offer,
        rationale="Generated by the deterministic degraded planner (LLM unavailable).",
        cited_guidelines=cited,
    )

    # _usable_segment already guaranteed within-cap, so create won't raise SegmentTooBroad.
    campaign = campaign_service.create_campaign(
        conn,
        draft,
        as_of_date=settings.as_of_date,
        max_reach=settings.max_segment_reach,
        trace_id=trace.trace_id,
    )

    trace.degraded = True
    trace.campaign_id = campaign.campaign_id
    trace.add_step(
        "tool",
        "create_campaign",
        "ok",
        summary=f"degraded created {campaign.campaign_id} (size={campaign.segment_size})",
    )
    return campaign
