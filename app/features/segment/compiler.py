from __future__ import annotations

from datetime import date, timedelta

from app.features.segment import dsl

_NUM_OPS = {"gte": ">=", "lte": "<=", "eq": "="}


def _placeholders(n: int) -> str:
    return ", ".join("?" * n)


def _compile_predicate(p: dsl.Predicate, as_of_date: str) -> tuple[str, list, bool]:
    """Return (condition_sql, params, needs_users_join) for one predicate."""
    if isinstance(p, (dsl.Country, dsl.Platform, dsl.Plan)):
        op = "IN" if p.op == "in" else "NOT IN"
        # values stored lowercased in the read copy → indexed equality, case-insensitive
        values = [v.lower() for v in p.values]
        return f"users.{p.field} {op} ({_placeholders(len(values))})", values, True

    if isinstance(p, dsl.LifecycleStage):
        op = "IN" if p.op == "in" else "NOT IN"
        return (
            f"user_metrics.lifecycle_stage {op} ({_placeholders(len(p.values))})",
            list(p.values),
            False,
        )

    if isinstance(p, (dsl.DaysSinceAppOpen, dsl.DaysSinceAnyEvent, dsl.PurchaseCount)):
        col = f"user_metrics.{p.field}"
        if p.op == "between":
            assert (
                p.value2 is not None
            )  # guaranteed by _NumPredicate._between_needs_upper
            return f"{col} BETWEEN ? AND ?", [p.value, p.value2], False
        return f"{col} {_NUM_OPS[p.op]} ?", [p.value], False

    if isinstance(p, dsl.IsPayer):
        return "user_metrics.is_payer = ?", [1 if p.value else 0], False

    if isinstance(p, (dsl.UsedFeature, dsl.NotUsedFeature)):
        negate = "NOT " if isinstance(p, dsl.NotUsedFeature) else ""
        sub = (
            "SELECT 1 FROM user_features "
            "WHERE user_features.user_id = user_metrics.user_id AND user_features.feature = ?"
        )
        return f"{negate}EXISTS ({sub})", [p.feature.lower()], False

    if isinstance(p, dsl.EventCount):
        sub = (
            "SELECT COUNT(*) FROM events "
            "WHERE events.user_id = user_metrics.user_id AND events.event_name = ?"
        )
        params: list = [p.event_name.lower()]
        if p.window_days is not None:
            threshold = (
                date.fromisoformat(as_of_date) - timedelta(days=p.window_days)
            ).isoformat()
            sub += " AND events.timestamp >= ?"
            params.append(threshold)
        op = ">=" if p.op == "gte" else "<="
        return f"({sub}) {op} ?", [*params, p.value], False

    raise ValueError(f"unsupported predicate field: {p.field!r}")  # pragma: no cover


def compile_segment(
    defn: dsl.SegmentDefinition, as_of_date: str
) -> tuple[str, str, list]:
    """Compile to ``(from_sql, where_sql, params)``."""
    conditions: list[str] = []
    params: list = []
    needs_users = False
    for predicate in defn.predicates:
        sql, predicate_params, join_users = _compile_predicate(predicate, as_of_date)
        conditions.append(sql)
        params.extend(predicate_params)
        needs_users = needs_users or join_users

    joiner = " AND " if defn.match == "all" else " OR "
    where_sql = f"({joiner.join(conditions)})" if conditions else "1=1"

    from_sql = "user_metrics"
    if needs_users:
        from_sql += " JOIN users ON users.user_id = user_metrics.user_id"
    return from_sql, where_sql, params
