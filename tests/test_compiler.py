from typing import Literal

import pytest
from pydantic import ValidationError

from app.features.segment import dsl
from app.features.segment.compiler import compile_segment

AS_OF = "2026-06-24"


def _compile(*predicates, match: Literal["all", "any"] = "all"):
    return compile_segment(
        dsl.SegmentDefinition(match=match, predicates=list(predicates)), AS_OF
    )


def test_profile_predicates_join_users_and_bind_values():
    from_sql, where, params = _compile(dsl.Country(values=["IN", "US"]))
    assert "JOIN users ON users.user_id = user_metrics.user_id" in from_sql
    assert where == "(users.country IN (?, ?))"
    assert params == [
        "in",
        "us",
    ]  # case-insensitive: values lowercased to match the lowercased copy

    _, where, params = _compile(dsl.Plan(op="not_in", values=["free"]))
    assert where == "(users.plan NOT IN (?))" and params == ["free"]


def test_numeric_predicates():
    _, where, params = _compile(dsl.DaysSinceAppOpen(op="between", value=14, value2=45))
    assert where == "(user_metrics.days_since_app_open BETWEEN ? AND ?)"
    assert params == [14, 45]

    _, where, params = _compile(dsl.PurchaseCount(op="gte", value=1))
    assert where == "(user_metrics.purchase_count >= ?)" and params == [1]


def test_is_payer_and_lifecycle():
    _, where, params = _compile(dsl.IsPayer(value=True))
    assert where == "(user_metrics.is_payer = ?)" and params == [1]

    _, where, params = _compile(dsl.LifecycleStage(values=["dormant", "churned"]))
    assert where == "(user_metrics.lifecycle_stage IN (?, ?))" and params == [
        "dormant",
        "churned",
    ]


def test_feature_predicates_use_exists_no_join():
    from_sql, where, params = _compile(dsl.UsedFeature(feature="voice_agent"))
    assert from_sql == "user_metrics"  # EXISTS subquery, no users join
    assert "EXISTS (SELECT 1 FROM user_features" in where
    assert "user_features.feature = ?" in where and params == ["voice_agent"]

    _, where, _ = _compile(dsl.NotUsedFeature(feature="journeys"))
    assert where.startswith("(NOT EXISTS")


def test_event_count_window_threshold():
    _, where, params = _compile(
        dsl.EventCount(event_name="purchase", window_days=30, op="gte", value=1)
    )
    assert "FROM events" in where and "events.event_name = ?" in where
    assert "events.timestamp >= ?" in where
    # window_days=30 → threshold is 30 days before the as-of date
    assert params == ["purchase", "2026-05-25", 1]


def test_values_are_case_insensitive():
    _, _, params = _compile(dsl.Country(values=["In", "us"]))
    assert params == ["in", "us"]
    # lifecycle stages normalize (and validate) case-insensitively
    assert dsl.LifecycleStage(values=["Dormant", "CHURNED"]).values == [
        "dormant",
        "churned",
    ]
    _, _, params = _compile(dsl.UsedFeature(feature="Voice_Agent"))
    assert params == ["voice_agent"]


def test_match_all_vs_any_and_empty():
    _, where, _ = _compile(
        dsl.IsPayer(value=True), dsl.PurchaseCount(op="gte", value=2), match="all"
    )
    assert " AND " in where
    _, where, _ = _compile(
        dsl.IsPayer(value=True), dsl.PurchaseCount(op="gte", value=2), match="any"
    )
    assert " OR " in where
    _, where, params = _compile()  # no predicates → matches everyone
    assert where == "1=1" and params == []


def test_validation_rejects_bad_definitions():
    with pytest.raises(ValidationError):
        dsl.LifecycleStage(values=["power_user"])  # not a known stage
    with pytest.raises(ValidationError):
        dsl.DaysSinceAppOpen(op="between", value=14)  # between needs value2
