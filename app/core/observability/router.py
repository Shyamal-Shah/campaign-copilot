from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.core.observability.trace import get_run

router = APIRouter(tags=["observability"])


@router.get("/runs/{trace_id}")
def get_run_trace(trace_id: str, request: Request) -> dict:
    conn = request.app.state.db
    run = get_run(conn, trace_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    if run.get("campaign_id"):
        from app.features.campaign import service as campaign_service

        campaign = campaign_service.get_campaign(conn, run["campaign_id"])
        run["campaign"] = campaign.model_dump() if campaign else None

    return run
