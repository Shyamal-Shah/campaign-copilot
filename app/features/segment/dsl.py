from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator

from app.features.segment.lifecycle import STAGES

SetOp = Literal["in", "not_in"]
NumOp = Literal["gte", "lte", "eq", "between"]


# --- profile predicates (on the user profile) ---
class _SetPredicate(BaseModel):
    op: SetOp = "in"
    values: list[str] = Field(min_length=1)


class Country(_SetPredicate):
    field: Literal["country"] = "country"


class Platform(_SetPredicate):
    field: Literal["platform"] = "platform"


class Plan(_SetPredicate):
    field: Literal["plan"] = "plan"


# --- behavioural-aggregate predicates (on user_metrics) ---
class LifecycleStage(BaseModel):
    field: Literal["lifecycle_stage"] = "lifecycle_stage"
    op: SetOp = "in"
    values: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _normalize_and_check(self):
        self.values = [
            v.strip().lower() for v in self.values
        ]  # stages are matched case-insensitively
        unknown = sorted(set(self.values) - set(STAGES))
        if unknown:
            raise ValueError(
                f"unknown lifecycle stage(s) {unknown}; valid: {list(STAGES)}"
            )
        return self


class _NumPredicate(BaseModel):
    op: NumOp
    value: int
    value2: int | None = None  # upper bound, required for `between`

    @model_validator(mode="after")
    def _between_needs_upper(self):
        if self.op == "between" and self.value2 is None:
            raise ValueError("`between` requires `value2` (the upper bound)")
        return self


class DaysSinceAppOpen(_NumPredicate):
    field: Literal["days_since_app_open"] = "days_since_app_open"


class DaysSinceAnyEvent(_NumPredicate):
    field: Literal["days_since_any_event"] = "days_since_any_event"


class PurchaseCount(_NumPredicate):
    field: Literal["purchase_count"] = "purchase_count"


class IsPayer(BaseModel):
    field: Literal["is_payer"] = "is_payer"
    value: bool


class UsedFeature(BaseModel):
    field: Literal["used_feature"] = "used_feature"
    feature: str


class NotUsedFeature(BaseModel):
    field: Literal["not_used_feature"] = "not_used_feature"
    feature: str


class EventCount(BaseModel):
    field: Literal["event_count"] = "event_count"
    event_name: str
    window_days: int | None = None  # None → all-time
    op: Literal["gte", "lte"] = "gte"
    value: int


Predicate = Annotated[
    Union[
        Country,
        Platform,
        Plan,
        LifecycleStage,
        DaysSinceAppOpen,
        DaysSinceAnyEvent,
        PurchaseCount,
        IsPayer,
        UsedFeature,
        NotUsedFeature,
        EventCount,
    ],
    Field(discriminator="field"),
]


class SegmentDefinition(BaseModel):
    match: Literal["all", "any"] = "all"
    predicates: list[Predicate] = Field(default_factory=list)
