from __future__ import annotations

from pydantic import BaseModel

from app.core.agent.types import AgentContext
from app.core.agent.executor import ToolExecutor, ToolSpec
from app.features.campaign import service as campaign_service
from app.features.campaign.models import CampaignDraft
from app.features.guidelines.service import search_guidelines as rag_search
from app.features.segment.dsl import SegmentDefinition
from app.features.segment.lifecycle import STAGES
from app.features.segment.service import preview_segment

# The DSL vocabulary advertised to the model so it builds valid predicates instead of guessing.
_PREDICATE_FIELDS = [
    {"field": "country|platform|plan", "ops": ["in", "not_in"], "on": "profile"},
    {"field": "lifecycle_stage", "ops": ["in", "not_in"], "values": list(STAGES)},
    {"field": "days_since_app_open|days_since_any_event", "ops": ["gte", "lte", "eq", "between"]},
    {"field": "purchase_count", "ops": ["gte", "lte", "eq", "between"]},
    {"field": "is_payer", "ops": ["value: true|false"]},
    {"field": "used_feature|not_used_feature", "args": ["feature"]},
    {"field": "event_count", "args": ["event_name", "window_days?", "op: gte|lte", "value"]},
]


# --- tool 1: describe_dataset (no LLM; anti-hallucination grounding of the vocabulary) ---
class DescribeDatasetArgs(BaseModel):
    """No arguments — returns the dataset's real vocabulary and size."""


def _distinct(db, col: str, table: str) -> list[str]:
    rows = db.execute(
        f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL ORDER BY {col}"
    ).fetchall()
    return [r[0] for r in rows]


def describe_dataset(ctx: AgentContext, args: DescribeDatasetArgs) -> dict:
    db = ctx.db
    try:
        events = [r[0] for r in db.execute(
            "SELECT DISTINCT event_name FROM events ORDER BY event_name"
        ).fetchall()]
    except Exception:
        events = []  # source events not reachable (e.g. an isolated test DB)
    total = db.execute("SELECT COUNT(*) FROM user_metrics").fetchone()[0]
    return {
        "total_users": total,
        "as_of_date": ctx.settings.as_of_date,
        "categoricals": {
            "country": _distinct(db, "country", "users"),
            "platform": _distinct(db, "platform", "users"),
            "plan": _distinct(db, "plan", "users"),
        },
        "lifecycle_stages": list(STAGES),
        "features": _distinct(db, "feature", "user_features"),
        "event_names": events,
        "predicate_fields": _PREDICATE_FIELDS,
        "note": "Build segments only from these fields/values; values are matched lower-case.",
    }


# --- tool 2: query_segment (DSL -> SQL count + sample; the grounded segment size) ---
def query_segment(ctx: AgentContext, args: SegmentDefinition) -> dict:
    result = preview_segment(
        ctx.db, args, ctx.settings.as_of_date, ctx.settings.max_segment_reach
    )
    return result.model_dump()


# --- tool 3: search_guidelines (RAG; cite real doc_ids) ---
class SearchGuidelinesArgs(BaseModel):
    query: str
    k: int | None = None


def search_guidelines(ctx: AgentContext, args: SearchGuidelinesArgs) -> dict:
    results = rag_search(args.query, args.k)
    return {
        "query": args.query,
        "results": [
            {"doc_id": r.doc_id, "title": r.title, "score": round(r.score, 4),
             "snippet": r.text[:300]}
            for r in results
        ],
    }


# --- tool 4: create_campaign (idempotent insert; grounds size + enforces reach cap) ---
def create_campaign(ctx: AgentContext, args: CampaignDraft) -> dict:
    # Run-level dedupe: if a campaign was already created this run, don't create a second.
    if ctx.campaign_id:
        return {"status": "already_created", "campaign_id": ctx.campaign_id}
    try:
        campaign = campaign_service.create_campaign(
            ctx.db, args,
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


def _summ_describe(p: dict) -> str:
    return f"{p.get('total_users')} users; {len(p.get('event_names', []))} event types"


def _summ_segment(p: dict) -> str:
    flag = " too_broad" if p.get("too_broad") else (" empty" if p.get("empty") else "")
    return f"count={p.get('count')} ({p.get('pct_of_base', 0):.1%}){flag}"


def _summ_search(p: dict) -> str:
    return f"{len(p.get('results', []))} docs: {[r['doc_id'] for r in p.get('results', [])]}"


def _summ_create(p: dict) -> str:
    return f"{p.get('status')} {p.get('campaign_id', p.get('error', ''))}"


def build_executor() -> ToolExecutor:
    """Register the four tools with their timeouts and trace summarizers."""
    return ToolExecutor([
        ToolSpec("describe_dataset",
                 "Return the dataset's real vocabulary (countries, platforms, plans, lifecycle "
                 "stages, features, event names) and the segment predicate fields. Call this first.",
                 DescribeDatasetArgs, describe_dataset, timeout_s=5.0, summarize=_summ_describe),
        ToolSpec("query_segment",
                 "Compile a structured SegmentDefinition to SQL and return the matching user count, "
                 "percentage of base, and a sample. Use this to size a segment before creating.",
                 SegmentDefinition, query_segment, timeout_s=10.0, summarize=_summ_segment),
        ToolSpec("search_guidelines",
                 "Retrieve marketing guideline passages for a query (cite the returned doc_ids). "
                 "Issue several targeted queries across facets (channel, copy limits, offers).",
                 SearchGuidelinesArgs, search_guidelines, timeout_s=15.0, summarize=_summ_search),
        ToolSpec("create_campaign",
                 "Create the campaign from a validated draft. Grounds segment_size in a real query "
                 "and rejects segments over the reach cap; returns the new campaign_id.",
                 CampaignDraft, create_campaign, timeout_s=10.0, summarize=_summ_create),
    ])
