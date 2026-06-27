"""Lifecycle stages, derived from event recency (guideline 17).

Single source of truth for the stage thresholds. Guideline 17 describes overlapping ranges; we resolve
them into mutually-exclusive day cutoffs so each user gets one primary stage. The same thresholds will
back the `lifecycle_stage` segment predicate.

Note: "power user" (high frequency + broad feature adoption) is a cross-cutting attribute rather than a
primary recency stage, so it is not assigned here.
"""

from __future__ import annotations

NEW_SIGNUP_DAYS = 7    # signed up within the last week
ACTIVE_MAX_DAYS = 7    # opened the app within the last week
LAPSING_MAX_DAYS = 14  # 8–14 days since an app_open
DORMANT_MAX_DAYS = 30  # 15–30 days; beyond this (or never active) is churned

STAGES = ("new", "active", "lapsing", "dormant", "churned")


def compute_stage(days_since_app_open: int | None, days_since_signup: int | None) -> str:
    """Assign one primary lifecycle stage from recency. ``None`` app-open recency = never active."""
    if days_since_signup is not None and days_since_signup <= NEW_SIGNUP_DAYS:
        return "new"
    if days_since_app_open is None:
        return "churned"
    if days_since_app_open <= ACTIVE_MAX_DAYS:
        return "active"
    if days_since_app_open <= LAPSING_MAX_DAYS:
        return "lapsing"
    if days_since_app_open <= DORMANT_MAX_DAYS:
        return "dormant"
    return "churned"
