from __future__ import annotations

from fastapi import APIRouter, Request

from app.features.segment.dsl import SegmentDefinition
from app.features.segment.service import SegmentResult, preview_segment
from app.shared.config import get_settings

router = APIRouter(tags=["segments"])


@router.post("/segments/preview", response_model=SegmentResult)
def preview(definition: SegmentDefinition, request: Request) -> SegmentResult:
    """Compile + run a segment definition and return its size, a sample, and sanity flags."""
    settings = get_settings()
    return preview_segment(
        request.app.state.db,
        definition,
        settings.as_of_date,
        settings.max_segment_reach,
    )
