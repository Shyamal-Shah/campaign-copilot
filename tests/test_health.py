from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["as_of_date"] == "2026-06-24"
    # config-presence flags are booleans; model_chain is a non-empty list
    assert isinstance(body["llm_configured"], bool)
    assert isinstance(body["embeddings_configured"], bool)
    assert isinstance(body["model_chain"], list) and body["model_chain"]
