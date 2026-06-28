from __future__ import annotations

from dataclasses import dataclass

from app.features.segment.dsl import (
    Country,
    IsPayer,
    LifecycleStage,
    SegmentDefinition,
)


@dataclass(frozen=True)
class GoldenCase:
    name: str
    goal: str
    kind: str = "create"  # "create" | "decline"

    # --- create-case expectations ---
    segment: SegmentDefinition | None = None
    min_count: int = 0
    max_count: int = 10**9
    channel: str = "push"
    rag_query: str | None = (
        None  # a fixture query (Tier A) / a realistic facet query (Tier B)
    )
    expect_docs: tuple[str, ...] = ()  # recall@k target: these doc_ids ⊆ retrieved
    # message copy the scripted (Tier-A) model proposes; channel limits are enforced by the tool.
    title: str = ""
    body: str = ""
    cta_text: str | None = None
    offer: dict | None = None

    # --- decline-case expectation ---
    decline_status: str = "unsupported"  # "unsupported" | "needs_clarification"


CASES: list[GoldenCase] = [
    GoldenCase(
        name="winback_us_push",
        goal=(
            "Win back lapsed and churned users in the US with a push notification and a "
            "discount offer to bring them back."
        ),
        segment=SegmentDefinition(
            match="all",
            predicates=[
                LifecycleStage(op="in", values=["lapsing", "dormant", "churned"]),
                Country(op="in", values=["US"]),
            ],
        ),
        min_count=400,
        max_count=900,
        channel="push",
        rag_query="win back churned users with a discount",
        expect_docs=("07", "13"),
        title="We saved a welcome-back deal for you",
        body="Come back and unlock a limited-time discount made just for you.",
        offer={"type": "discount", "value": "10%", "expiry_days": 7},
    ),
    GoldenCase(
        name="onboarding_new_inapp",
        goal="Onboard brand-new signups with a warm welcome in-app message.",
        segment=SegmentDefinition(
            match="all", predicates=[LifecycleStage(op="in", values=["new"])]
        ),
        min_count=300,
        max_count=900,
        channel="in_app",
        rag_query="onboarding new signups",
        expect_docs=("06",),
        title="Welcome aboard!",
        body="Here's a quick tip to get the most out of your first week with us.",
        cta_text="Show me how",
    ),
    GoldenCase(
        name="active_payers_push",
        goal="Send a short push notification to our paying users.",
        segment=SegmentDefinition(match="all", predicates=[IsPayer(value=True)]),
        min_count=700,
        max_count=900,
        channel="push",
        rag_query="push notification character limit",
        expect_docs=("03",),
        title="A thank-you for being a Pro member",
        body="Tap to see what's new in your plan today.",
    ),
    GoldenCase(
        name="lookalike_unsupported",
        goal="Target users who look like our best customers using a lookalike model.",
        kind="decline",
        decline_status="unsupported",
    ),
]
