from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from conftest import build_db, make_app

from app.features.campaign import models

US_ONLY = {
    "match": "all",
    "predicates": [{"field": "country", "op": "in", "values": ["US"]}],
}
EVERYONE = {
    "match": "all",
    "predicates": [
        {"field": "country", "op": "in", "values": ["US", "IN", "NG", "DE", "BR"]}
    ],
}


def _draft(segment=US_ONLY, **over) -> dict:
    body = {
        "name": "Winback US",
        "goal": "win back US users",
        "segment": segment,
        "message": {"kind": "push", "title": "We miss you", "body": "Here's 10% off"},
        "offer": {"type": "discount", "value": "10%"},
        "rationale": "lapsed US users respond to a small incentive",
        "cited_guidelines": ["07", "13"],
    }
    body.update(over)
    return body


@pytest.fixture
def client(tmp_path):
    return TestClient(make_app(build_db(tmp_path)))


# --- idempotency state machine --------------------------------------------------------------------
def test_missing_idempotency_key_is_400(client):
    r = client.post("/campaigns", json=_draft())
    assert r.status_code == 400


def test_first_create_then_same_key_replays_one_row(client):
    h = {"Idempotency-Key": "k-123"}
    first = client.post("/campaigns", json=_draft(), headers=h)
    assert first.status_code == 201
    assert first.json()["already_exists"] is False
    cid = first.json()["campaign_id"]

    # Same key, even with a different body, returns the original — exactly one row exists.
    second = client.post("/campaigns", json=_draft(name="DIFFERENT"), headers=h)
    assert second.status_code == 200
    assert second.json()["already_exists"] is True
    assert second.json()["campaign_id"] == cid
    assert second.json()["name"] == "Winback US"  # not "DIFFERENT"
    assert len(client.get("/campaigns").json()) == 1


def test_in_progress_key_is_409(client):
    client.app.state.db.execute(
        "INSERT INTO idempotency_keys (key, status, created_at) VALUES ('busy', 'in_progress', 'now')"
    )
    client.app.state.db.commit()
    r = client.post("/campaigns", json=_draft(), headers={"Idempotency-Key": "busy"})
    assert r.status_code == 409


def test_too_broad_segment_is_rejected_and_key_released(client):
    h = {"Idempotency-Key": "k-broad"}
    r = client.post("/campaigns", json=_draft(segment=EVERYONE), headers=h)
    assert r.status_code == 400
    assert "reach" in r.json()["detail"].lower()
    # Released, so the same key is reusable for a valid request (not stuck in_progress -> 409).
    again = client.post("/campaigns", json=_draft(), headers=h)
    assert again.status_code == 201


def test_get_404_and_list(client):
    assert client.get("/campaigns/nope").status_code == 404
    client.post("/campaigns", json=_draft(), headers={"Idempotency-Key": "k1"})
    cid = client.get("/campaigns").json()[0]["campaign_id"]
    got = client.get(f"/campaigns/{cid}").json()
    assert got["segment_size"] == 3  # 3 of 10 are US — grounded, not model-supplied
    assert got["cited_guidelines"] == ["07", "13"]


# --- validators (grounding made mechanical) -------------------------------------------------------
def test_push_limits_enforced():
    with pytest.raises(ValidationError):
        models.PushMessage(title="x" * 51, body="ok")
    with pytest.raises(ValidationError):
        models.PushMessage(title="ok", body="y" * 121)


def test_link_must_be_https():
    with pytest.raises(ValidationError):
        models.PushMessage(
            title="ok", body="ok", image_url="http://cdn.example.com/a.png"
        )
    # https is accepted when no allowlist is configured (HTTPS-only mode)
    ok = models.PushMessage(
        title="ok", body="ok", image_url="https://cdn.example.com/a.png"
    )
    assert ok.image_url.startswith("https://")


def test_channel_and_image_url_derive_from_message():
    d = models.CampaignDraft.model_validate(_draft())
    assert d.channel == "push"
    assert d.image_url is None
