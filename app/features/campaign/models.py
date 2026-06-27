from __future__ import annotations

from typing import Annotated, Literal, Union
from urllib.parse import urlparse

from pydantic import BaseModel, Field, computed_field, field_validator

from app.features.segment.dsl import SegmentDefinition
from app.shared.config import get_settings

Channel = Literal["push", "email", "in_app"]


# --- channel-specific message bodies (discriminated by `kind`) ---
class PushMessage(BaseModel):
    kind: Literal["push"] = "push"
    title: str = Field(max_length=50)  # doc-03 hard limit
    body: str = Field(max_length=120)  # doc-03 hard limit
    image_url: str | None = None
    deep_link: str | None = None


class EmailMessage(BaseModel):
    kind: Literal["email"] = "email"
    subject: str = Field(max_length=120)
    preheader: str | None = Field(default=None, max_length=120)
    body: str
    deep_link: str | None = None


class InAppMessage(BaseModel):
    kind: Literal["in_app"] = "in_app"
    title: str = Field(max_length=60)
    body: str = Field(max_length=240)
    cta_text: str | None = Field(default=None, max_length=30)
    deep_link: str | None = None


CampaignMessage = Annotated[
    Union[PushMessage, EmailMessage, InAppMessage],
    Field(discriminator="kind"),
]


class Offer(BaseModel):
    """An optional incentive attached to the campaign (doc-13 gating is soft-validated)."""

    type: str  # e.g. "discount", "free_trial", "credit"
    value: str  # e.g. "10%", "7 days"
    expiry_days: int | None = Field(default=None, ge=1)
    eligibility_note: str | None = None


class CampaignDraft(BaseModel):
    """The agent's final campaign proposal and the body of a direct ``POST /campaigns``.

    ``channel`` and ``image_url`` are derived from the message so they can never disagree with it.
    """

    name: str = Field(min_length=1, max_length=120)
    goal: str
    segment: SegmentDefinition
    message: CampaignMessage
    offer: Offer | None = None
    rationale: str = ""
    cited_guidelines: list[str] = Field(
        default_factory=list
    )  # guideline doc_ids backing the draft

    @field_validator("cited_guidelines")
    @classmethod
    def _dedupe_citations(cls, v: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for d in v:
            seen.setdefault(d, None)
        return list(seen)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def channel(self) -> Channel:
        return self.message.kind

    @computed_field  # type: ignore[prop-decorator]
    @property
    def image_url(self) -> str | None:
        return getattr(self.message, "image_url", None)


class Campaign(BaseModel):
    """A persisted campaign as returned by the API."""

    campaign_id: str
    name: str
    goal: str
    channel: Channel
    status: str
    segment_size: int
    segment: SegmentDefinition
    message: CampaignMessage
    offer: Offer | None
    image_url: str | None
    cited_guidelines: list[str]
    trace_id: str | None
    created_at: str
