import sqlite3

from typing import Literal

from app.features.segment import dsl
from app.features.segment.metrics import build_metrics
from app.features.segment.service import preview_segment
from app.shared.config import get_settings
from app.shared.db import connect_app, init_schema

AS_OF = "2026-06-24"
MAX_REACH = 0.85


def _synthetic_source(tmp_path, users, events) -> str:
    path = tmp_path / "source.sqlite"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE users (user_id TEXT PRIMARY KEY, signup_date TEXT, country TEXT, "
        "platform TEXT, app_version TEXT, plan TEXT)"
    )
    con.execute(
        "CREATE TABLE events (event_id TEXT PRIMARY KEY, user_id TEXT, event_name TEXT, "
        "timestamp TEXT, properties TEXT)"
    )
    con.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)", users)
    con.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?)", events)
    con.commit()
    con.close()
    return str(path)


def _built(tmp_path, users, events) -> sqlite3.Connection:
    conn = connect_app(":memory:")
    init_schema(conn)
    build_metrics(conn, _synthetic_source(tmp_path, users, events), AS_OF)
    return conn


def _preview(conn, *predicates, match: Literal["all", "any"] = "all"):
    return preview_segment(
        conn,
        dsl.SegmentDefinition(match=match, predicates=list(predicates)),
        AS_OF,
        MAX_REACH,
    )


def test_recency_window_selects_exactly_the_in_window_users(tmp_path):
    users = [(f"u_{x}", "2025-01-01", "US", "iOS", "3.4.0", "free") for x in "abcd"]
    events = [
        (
            "e1",
            "u_a",
            "app_open",
            "2026-06-20T10:00:00Z",
            "{}",
        ),  # 4 days ago  → active, out of window
        (
            "e2",
            "u_b",
            "app_open",
            "2026-06-04T10:00:00Z",
            "{}",
        ),  # 20 days ago → in [14, 30]
        (
            "e3",
            "u_c",
            "app_open",
            "2026-03-16T10:00:00Z",
            "{}",
        ),  # ~100 days   → out of window
        # u_d has no events → null recency → out of window
    ]
    conn = _built(tmp_path, users, events)
    res = _preview(conn, dsl.DaysSinceAppOpen(op="between", value=14, value2=30))
    assert res.count == 1
    assert res.total_users == 4
    assert [u.user_id for u in res.users] == ["u_b"]
    assert not res.empty and not res.too_broad


def test_empty_and_too_broad_flags(tmp_path):
    users = [(f"u_{i}", "2025-01-01", "US", "iOS", "3.4.0", "free") for i in range(3)]
    conn = _built(tmp_path, users, [])  # nobody is a payer; nobody is active

    empty = _preview(conn, dsl.IsPayer(value=True))
    assert empty.count == 0 and empty.empty and not empty.too_broad

    broad = _preview(conn)  # no predicates → everyone
    assert broad.count == 3 and broad.pct_of_base == 1.0 and broad.too_broad


def test_lifecycle_and_feature_filters(tmp_path):
    users = [
        ("u_active", "2025-01-01", "US", "iOS", "3.4.0", "free"),
        ("u_churn", "2025-01-01", "US", "iOS", "3.4.0", "free"),
    ]
    events = [
        ("e1", "u_active", "app_open", "2026-06-23T10:00:00Z", "{}"),
        (
            "e2",
            "u_active",
            "feature_used",
            "2026-06-23T10:05:00Z",
            '{"feature_name": "voice_agent"}',
        ),
    ]
    conn = _built(tmp_path, users, events)

    churned = _preview(conn, dsl.LifecycleStage(values=["Churned"]))  # case-insensitive
    assert [u.user_id for u in churned.users] == ["u_churn"]

    used = _preview(conn, dsl.UsedFeature(feature="voice_agent"))
    assert used.count == 1 and used.users[0].user_id == "u_active"

    not_used = _preview(conn, dsl.NotUsedFeature(feature="voice_agent"))
    assert {u.user_id for u in not_used.users} == {"u_churn"}


def test_profile_filter_is_local_and_case_insensitive(tmp_path):
    users = [
        ("u_in", "2025-01-01", "IN", "Android", "3.4.0", "free"),
        ("u_us", "2025-01-01", "US", "iOS", "3.4.0", "pro"),
    ]
    conn = _built(
        tmp_path, users, []
    )  # profile predicates hit the local lowercased `users` copy

    res = _preview(
        conn, dsl.Country(values=["in"])
    )  # lowercase input matches the stored "IN" → "in"
    assert [u.user_id for u in res.users] == ["u_in"]

    res = _preview(
        conn, dsl.Plan(op="not_in", values=["FREE"])
    )  # mixed case still matches
    assert [u.user_id for u in res.users] == ["u_us"]


def test_winback_example_on_real_data_is_in_expected_range():
    """The assignment's win-back goal (active last month, not opened in 14 days) → ~996–1514 users."""
    s = get_settings()
    conn = connect_app(":memory:")
    init_schema(conn)
    build_metrics(conn, s.source_db_path, s.as_of_date)

    res = _preview(conn, dsl.DaysSinceAppOpen(op="between", value=14, value2=30))
    assert 996 <= res.count <= 1514
    assert 0 < res.pct_of_base < 0.85
