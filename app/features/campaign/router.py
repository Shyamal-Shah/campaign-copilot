from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, Response

from app.features.campaign import idempotency, service
from app.features.campaign.models import Campaign, CampaignDraft
from app.shared.config import get_settings

router = APIRouter(tags=["campaigns"])


class CampaignCreateResponse(Campaign):
    """A created campaign plus whether this call replayed an earlier reservation."""

    already_exists: bool = False


@router.post("/campaigns", response_model=CampaignCreateResponse, status_code=201)
def create(
    draft: CampaignDraft,
    request: Request,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> CampaignCreateResponse:
    conn = request.app.state.db
    settings = get_settings()

    try:
        reservation = idempotency.reserve(conn, idempotency_key)
    except idempotency.IdempotencyConflict:
        raise HTTPException(
            status_code=409, detail="a request with this Idempotency-Key is in progress"
        )

    if reservation.status == "completed":
        response.status_code = 200
        return CampaignCreateResponse.model_validate_json(
            reservation.response_json
        ).model_copy(update={"already_exists": True})

    # We own the reservation: do the work, then either complete it or release it for a retry.
    try:
        campaign = service.create_campaign(
            conn,
            draft,
            as_of_date=settings.as_of_date,
            max_reach=settings.max_segment_reach,
        )
    except service.SegmentTooBroad as exc:
        idempotency.release(conn, idempotency_key)
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        idempotency.release(conn, idempotency_key)
        raise

    body = CampaignCreateResponse(**campaign.model_dump(), already_exists=False)
    idempotency.complete(
        conn,
        idempotency_key,
        response_json=campaign.model_dump_json(),
        campaign_id=campaign.campaign_id,
    )
    return body


@router.get("/campaigns/{campaign_id}", response_model=Campaign)
def get(campaign_id: str, request: Request) -> Campaign:
    campaign = service.get_campaign(request.app.state.db, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    return campaign


@router.get("/campaigns", response_model=list[Campaign])
def list_all(request: Request) -> list[Campaign]:
    return service.list_campaigns(request.app.state.db)
