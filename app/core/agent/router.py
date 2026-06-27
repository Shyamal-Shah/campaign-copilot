from __future__ import annotations

from time import perf_counter

from agents import Runner
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response
from pydantic import BaseModel

from app.core.agent.types import AgentContext, CopilotOutcome
from app.core.observability import trace as tracing
from app.core.observability.logging import log_event
from app.features.campaign import idempotency
from app.features.campaign import service as campaign_service
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


async def _run_agent(
    agent, goal: str, conn, settings, idempotency_key: str, ctx: AgentContext
) -> None:
    """Background task: run the agent, persist the trace, complete the reservation."""
    trace = ctx.trace
    t0 = perf_counter()
    try:
        result = await Runner.run(agent, goal, context=ctx, max_turns=settings.max_turns)
        outcome: CopilotOutcome = result.final_output
        usage = result.context_wrapper.usage
        trace.total_tokens = getattr(usage, "total_tokens", 0) or 0
        trace.total_requests = getattr(usage, "requests", 0) or 0
    except Exception as exc:
        idempotency.release(conn, idempotency_key)
        trace.status = "error"
        trace.total_ms = _ms(t0)
        exc_repr = repr(exc)
        trace.add_step(
            "model",
            "runner",
            "error",
            summary=type(exc).__name__,
            detail={"error": exc_repr},
        )
        tracing.persist(conn, trace)
        log_event(trace.trace_id, "run_failed", error=exc_repr)
        return

    trace.total_ms = _ms(t0)
    trace.status = outcome.status
    trace.est_cost = round(trace.total_tokens / 1000 * settings.cost_per_1k_tokens, 6)
    trace.message = outcome.message

    if outcome.status == "created":
        if ctx.campaign_id is None:
            # Model claimed success but never called create_campaign — refuse rather than lie.
            idempotency.release(conn, idempotency_key)
            trace.status = "error"
            tracing.persist(conn, trace)
            log_event(
                trace.trace_id,
                "run_failed",
                error="agent reported created but no campaign was persisted",
            )
            return
        trace.campaign_id = ctx.campaign_id

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
        status=outcome.status,
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
    tracing.persist(conn, trace)

    ctx = AgentContext(db=conn, settings=settings, trace=trace)
    background_tasks.add_task(
        _run_agent, agent, req.goal, conn, settings, idempotency_key, ctx
    )

    return CopilotRunAccepted(
        trace_id=trace.trace_id,
        idempotency={"key": idempotency_key, "state": "accepted"},
    )
