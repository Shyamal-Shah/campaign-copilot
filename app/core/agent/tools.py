from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.core.agent.types import PlannerState
from app.core.agent.executor import ToolExecutor, ToolSpec
from app.features.campaign import service as campaign_service
from app.features.campaign.models import (
    CampaignDraft,
    CampaignMessage,
    Channel,
    EmailMessage,
    InAppMessage,
    Offer,
    PushMessage,
)
from app.features.guidelines.service import search_guidelines as rag_search
from app.features.segment.dsl import SegmentDefinition
from app.features.segment.service import preview_segment


# --- tool 1: query_segment (DSL -> SQL count + sample; the grounded segment size) ---
def query_segment(ctx: PlannerState, args: SegmentDefinition) -> dict:
    result = preview_segment(
        ctx.db, args, ctx.settings.as_of_date, ctx.settings.max_segment_reach
    )
    if not result.empty and not result.too_broad:
        ctx.segment = args
    return result.model_dump()


# --- tool 2: search_guidelines (RAG; cite real doc_ids) ---
class SearchGuidelinesArgs(BaseModel):
    query: str
    k: int | None = None


def search_guidelines(ctx: PlannerState, args: SearchGuidelinesArgs) -> dict:
    results = rag_search(args.query, args.k)
    return {
        "query": args.query,
        "results": [
            {"doc_id": r.doc_id, "title": r.title, "score": round(r.score, 4),
             "text": r.text}
            for r in results
        ],
    }


# --- tool 3: create_campaign (idempotent insert; grounds size + enforces reach cap) ---
class CreateCampaignArgs(BaseModel):
    """Payload for creating a campaign.

    - The audience segment is read from session state populated by the last ``query_segment`` call.
    - Message fields (``title``/``body``/…) are provided flat and assembled into the appropriate channel structure internally.
    """

    name: str = Field(min_length=1, max_length=120)
    channel: Channel = "push"
    title: str = ""  # push/in_app headline; for email, the subject line is used
    body: str
    subject: str | None = None  # email only (falls back to title)
    preheader: str | None = None  # email only
    cta_text: str | None = None  # in_app only
    image_url: str | None = None  # push only
    deep_link: str | None = None
    offer: Offer | None = None
    rationale: str = ""
    cited_guidelines: list[str] = Field(default_factory=list)


def _build_message(args: CreateCampaignArgs) -> CampaignMessage:
    """Assemble the channel-appropriate message from flat args (length limits enforced here)."""
    if args.channel == "email":
        return EmailMessage(
            subject=args.subject or args.title, preheader=args.preheader,
            body=args.body, deep_link=args.deep_link,
        )
    if args.channel == "in_app":
        return InAppMessage(
            title=args.title, body=args.body, cta_text=args.cta_text, deep_link=args.deep_link,
        )
    return PushMessage(
        title=args.title, body=args.body, image_url=args.image_url, deep_link=args.deep_link,
    )


def create_campaign(ctx: PlannerState, args: CreateCampaignArgs) -> dict:
    # Run-level dedupe: if a campaign was already created this run, don't create a second.
    if ctx.campaign_id:
        return {"status": "already_created", "campaign_id": ctx.campaign_id}
    if ctx.segment is None:
        return {
            "status": "error",
            "error": "no_segment",
            "detail": "call query_segment first to define and size the audience",
        }
    try:
        # Channel/limit violations (e.g. push title >50) become a typed, correctable error.
        message = _build_message(args)
    except ValidationError as exc:
        return {"status": "error", "error": "invalid_message", "detail": json.loads(exc.json())}
    draft = CampaignDraft(
        # Honor the marketer's requested name verbatim when they gave one; otherwise the model's.
        name=ctx.requested_name or args.name,
        goal=ctx.trace.goal,
        segment=ctx.segment,  # grounded: the exact segment query_segment sized
        message=message,
        offer=args.offer,
        rationale=args.rationale,
        cited_guidelines=args.cited_guidelines,
    )
    try:
        campaign = campaign_service.create_campaign(
            ctx.db, draft,
            as_of_date=ctx.settings.as_of_date,
            max_reach=ctx.settings.max_segment_reach,
            trace_id=ctx.trace.trace_id,
        )
    except campaign_service.SegmentTooBroad as exc:
        return {"status": "error", "error": "segment_too_broad", "detail": str(exc)}
    ctx.campaign_id = campaign.campaign_id
    ctx.trace.campaign_id = campaign.campaign_id
    return {
        "status": "created",
        "campaign_id": campaign.campaign_id,
        "channel": campaign.channel,
        "segment_size": campaign.segment_size,
    }


# --- tool 4: finish (terminal decline; the run's outcome when no campaign is created) ---
class FinishArgs(BaseModel):
    status: Literal["unsupported", "needs_clarification"]
    message: str = ""


def finish(ctx: PlannerState, args: FinishArgs) -> dict:
    """End the run WITHOUT a campaign. Records the decline on PlannerState so the router reports it.

    Use ``unsupported`` when the goal can't be expressed in the segment DSL or isn't a campaign
    request, ``needs_clarification`` (with one specific question in ``message``) when it's ambiguous.
    """
    ctx.finish_status = args.status
    ctx.finish_message = args.message
    return {"status": args.status, "message": args.message}


def _summ_segment(p: dict) -> str:
    flag = " too_broad" if p.get("too_broad") else (" empty" if p.get("empty") else "")
    return f"count={p.get('count')} ({p.get('pct_of_base', 0):.1%}){flag}"


def _summ_search(p: dict) -> str:
    return f"{len(p.get('results', []))} docs: {[r['doc_id'] for r in p.get('results', [])]}"


def _summ_create(p: dict) -> str:
    return f"{p.get('status')} {p.get('campaign_id', p.get('error', ''))}"


def _summ_finish(p: dict) -> str:
    return f"{p.get('status')}: {p.get('message', '')[:60]}"


def build_executor() -> ToolExecutor:
    """Register the four tools with their timeouts and trace summarizers."""
    return ToolExecutor([
        ToolSpec("query_segment",
                 "Compile a structured SegmentDefinition to SQL and return the matching user count, "
                 "percentage of base, and a sample. Use this to size a segment before creating.",
                 SegmentDefinition, query_segment, timeout_s=10.0, summarize=_summ_segment),
        ToolSpec("search_guidelines",
                 "Retrieve marketing guideline passages for a query (cite the returned doc_ids). "
                 "Issue several targeted queries across facets (channel, copy limits, offers).",
                 SearchGuidelinesArgs, search_guidelines, timeout_s=15.0, summarize=_summ_search),
        ToolSpec("create_campaign",
                 "Create the campaign for the audience you most recently sized with query_segment "
                 "(do NOT pass the segment again — it's taken from that result). Give flat fields: "
                 "channel (push|email|in_app, default push), title and body as separate strings "
                 "(push: title ≤50, body ≤120), optional offer, rationale, and cited_guidelines "
                 "doc_ids. Returns the new campaign_id; calling this successfully ENDS the run.",
                 CreateCampaignArgs, create_campaign, timeout_s=10.0, summarize=_summ_create),
        ToolSpec("finish",
                 "End the run WITHOUT creating a campaign. Use status='unsupported' when the goal "
                 "can't be expressed in the segment DSL or isn't a campaign request, or "
                 "'needs_clarification' with one specific question. This ENDS the run.",
                 FinishArgs, finish, timeout_s=5.0, summarize=_summ_finish),
    ])
