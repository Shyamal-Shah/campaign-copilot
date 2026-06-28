from __future__ import annotations

import asyncio
from time import perf_counter

from agents import Runner
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response
from pydantic import BaseModel

from app.core.agent import fallback
from app.core.agent.hooks import TracingHooks
from app.core.agent.types import PlannerState
from app.core.observability import trace as tracing
from app.core.observability.logging import log_event
from app.features.campaign import idempotency
from app.shared.config import get_settings

router = APIRouter(tags=["copilot"])


class CopilotRunRequest(BaseModel):
    goal: str
    name: str | None = None
    channel_hint: str | None = None


class CopilotRunAccepted(BaseModel):
    trace_id: str
    idempotency: (
        dict  # {key, state: "accepted" | "already_exists", ...cached fields on replay}
    )


def _ms(t0: float) -> float:
    return (perf_counter() - t0) * 1000


def _compose_input(goal: str, channel_hint: str | None) -> str:
    """The text handed to the agent. The campaign name is applied deterministically in
    create_campaign (from PlannerState), so only the channel hint needs to reach the model —
    it must influence both the channel choice and the copy it writes for that channel.
    """
    if not channel_hint:
        return goal
    return (
        f"{goal}\n\n"
        f"Preferred channel: {channel_hint}. Create the campaign on this channel and write "
        f"copy appropriate for it, unless the retrieved guidelines clearly advise otherwise."
    )


def _finalize_degraded(
    goal: str,
    conn,
    settings,
    idempotency_key: str,
    ctx: PlannerState,
    t0: float,
    *,
    reason: str,
) -> None:
    """Recover via the deterministic planner and finalize the run.

    Shared by both no-campaign outcomes: the agent path raising outright, and the agent
    completing with status="created" but never persisting a campaign. ``reason`` is the
    underlying cause, logged on the failed-to-degrade path.
    """
    trace = ctx.trace
    log_event(trace.trace_id, "run_degraded_start", error=reason)
    campaign = fallback.run_degraded(
        goal, conn, settings, trace, name=ctx.requested_name
    )
    trace.total_ms = _ms(t0)
    if campaign is None:
        idempotency.release(conn, idempotency_key)
        trace.status = "error"
        tracing.persist(conn, trace)
        log_event(trace.trace_id, "run_failed", error=reason)
        return
    trace.status = "created"
    trace.message = "Created via the degraded planner."
    trace.est_cost = round(trace.total_tokens / 1000 * settings.cost_per_1k_tokens, 6)
    tracing.persist(conn, trace)
    idempotency.complete(
        conn,
        idempotency_key,
        response_json=trace.model_dump_json(),
        campaign_id=trace.campaign_id,
    )
    log_event(
        trace.trace_id,
        "run_degraded",
        status="created",
        campaign_id=trace.campaign_id,
    )


async def _run_agent(
    agent, goal: str, conn, settings, idempotency_key: str, ctx: PlannerState
) -> None:
    """Background task: run the agent, persist the trace, complete the reservation."""
    trace = ctx.trace
    t0 = perf_counter()
    try:
        # Wall-clock backstop: free/open models are slow, so the budget is generous (config), but a
        # genuinely hung run still terminates. On expiry asyncio raises TimeoutError → degraded path.
        result = await asyncio.wait_for(
            Runner.run(
                agent,
                _compose_input(goal, ctx.channel_hint),
                context=ctx,
                max_turns=settings.max_turns,
                hooks=TracingHooks(trace),
            ),
            timeout=settings.run_budget_s,
        )
        usage = result.context_wrapper.usage
        trace.total_tokens = getattr(usage, "total_tokens", 0) or 0
        trace.total_requests = getattr(usage, "requests", 0) or 0
    except Exception as exc:
        # The agent path failed outright (budget exceeded, every model down, max_turns, bad output).
        # Fall back to the deterministic degraded planner: a grounded, idempotent campaign, no LLM.
        exc_repr = repr(exc)
        trace.add_step(
            "model",
            "runner",
            "error",
            summary=type(exc).__name__,
            detail={"error": exc_repr},
        )
        _finalize_degraded(
            goal, conn, settings, idempotency_key, ctx, t0, reason=exc_repr
        )
        return

    trace.total_ms = _ms(t0)
    trace.est_cost = round(trace.total_tokens / 1000 * settings.cost_per_1k_tokens, 6)

    # Outcome is derived from PlannerState (real tool effects), never the model's words: a campaign
    # exists only if create_campaign persisted one; a decline is real only if finish recorded it.
    if ctx.campaign_id:
        trace.status = "created"
        trace.campaign_id = ctx.campaign_id
        trace.message = "Campaign created."
    elif ctx.finish_status:
        trace.status = ctx.finish_status  # "unsupported" | "needs_clarification"
        trace.message = ctx.finish_message
    else:
        # The loop ended without create_campaign or finish (e.g. the model emitted plain text
        # instead of calling a terminal tool). Fail honestly rather than inventing a result.
        final_output = getattr(result, "final_output", None)
        detail: dict = {
            "final_output": str(final_output)[:2000] if final_output else None,
            "new_items_count": len(getattr(result, "new_items", None) or []),
        }
        idempotency.release(conn, idempotency_key)
        trace.status = "error"
        trace.add_step(
            "model",
            "no_terminal_action",
            "error",
            summary="planner ended without calling a terminal tool",
            detail=detail,
        )
        tracing.persist(conn, trace)
        log_event(
            trace.trace_id,
            "run_failed",
            error="planner ended without creating a campaign or declining",
            final_output=str(final_output)[:500] if final_output else None,
        )
        return

    tracing.persist(conn, trace)
    idempotency.complete(
        conn,
        idempotency_key,
        response_json=trace.model_dump_json(),
        campaign_id=ctx.campaign_id,
    )
    log_event(
        trace.trace_id,
        "run_complete",
        status=trace.status,
        campaign_id=ctx.campaign_id,
        tokens=trace.total_tokens,
    )


@router.post("/copilot/run", status_code=202, response_model=CopilotRunAccepted)
async def run(
    req: CopilotRunRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> CopilotRunAccepted:
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="LLM is not configured")
    conn = request.app.state.db
    settings = get_settings()

    # Reserve before any LLM work.
    try:
        reservation = idempotency.reserve(conn, idempotency_key)
    except idempotency.IdempotencyConflict:
        raise HTTPException(
            status_code=409, detail="a request with this Idempotency-Key is in progress"
        )

    if reservation.status == "completed":
        # Already done — return the persisted trace_id immediately.
        cached = tracing.RunTrace.model_validate_json(reservation.response_json or "{}")
        response.status_code = 200
        return CopilotRunAccepted(
            trace_id=cached.trace_id,
            idempotency={"key": idempotency_key, "state": "already_exists"},
        )

    # Persist a placeholder trace so GET /runs/{trace_id} returns in_progress immediately.
    trace = tracing.RunTrace(goal=req.goal)
    trace.add_step(
        "note",
        "run_started",
        "ok",
        summary=req.goal[:80],
        detail={"name": req.name, "channel_hint": req.channel_hint},
    )
    tracing.persist(conn, trace)

    ctx = PlannerState(
        db=conn,
        settings=settings,
        trace=trace,
        requested_name=req.name,
        channel_hint=req.channel_hint,
    )
    background_tasks.add_task(
        _run_agent, agent, req.goal, conn, settings, idempotency_key, ctx
    )

    return CopilotRunAccepted(
        trace_id=trace.trace_id,
        idempotency={"key": idempotency_key, "state": "accepted"},
    )
