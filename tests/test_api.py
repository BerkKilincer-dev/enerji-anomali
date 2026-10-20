import pytest
from fastapi.testclient import TestClient

from src.api import main
from src.api.repository import DemoRepository
from src.config import get_settings

KEY = "test-key-0123456789"


@pytest.fixture
def client(monkeypatch, fresh_settings):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("API_KEY", KEY)
    main.app.dependency_overrides[main.get_repository] = DemoRepository
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_read_endpoints_follow_the_contract(client):
    assert client.get("/health").json() == {"status": "ok", "mode": "demo"}
    buildings = client.get("/buildings").json()
    assert {b["building_id"] for b in buildings} == {100000, 100001}
    readings = client.get("/buildings/100000/readings", params={"hours": 24}).json()
    forecast = client.get("/buildings/100000/forecast", params={"hours": 24}).json()
    assert len(readings) == len(forecast) == 24
    assert readings[-1]["ts"] == forecast[-1]["ts"]
    assert {"id", "ts_start", "explanation", "est_cost_tl", "verdict"} <= set(
        client.get("/anomalies").json()[0]
    )
    assert client.get("/anomalies", params={"building_id": 100001}).json() == []
    assert client.get("/summary").json()["buildings"] == 2


def test_bad_inputs(client):
    assert client.get("/buildings/1/readings").status_code == 404
    assert client.get("/buildings/100000/readings", params={"hours": 0}).status_code == 422
    assert client.get("/anomalies", params={"limit": 10_000}).status_code == 422


def test_writes_need_a_valid_key(client, monkeypatch):
    body = {"verdict": "confirmed"}
    assert client.post("/anomalies/1/feedback", json=body).status_code == 401
    assert (
        client.post("/anomalies/1/feedback", json=body, headers={"X-API-Key": "wrong"}).status_code
        == 401
    )
    # Demo mode has nowhere to store writes.
    assert (
        client.post("/anomalies/1/feedback", json=body, headers={"X-API-Key": KEY}).status_code
        == 503
    )
    assert (
        client.post(
            "/anomalies/1/feedback", json={"verdict": "maybe"}, headers={"X-API-Key": KEY}
        ).status_code
        == 422
    )
    monkeypatch.delenv("API_KEY")
    get_settings.cache_clear()
    assert (
        client.post("/anomalies/1/feedback", json=body, headers={"X-API-Key": KEY}).status_code
        == 503
    )


def test_register_validates_body(client):
    headers = {"X-API-Key": KEY}
    assert (
        client.post("/buildings", json={"external_id": "has space"}, headers=headers).status_code
        == 422
    )
    assert (
        client.post(
            "/buildings", json={"external_id": "ok", "timezone": "Mars/Base"}, headers=headers
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/buildings", json={"external_id": "ok", "lat": 120}, headers=headers
        ).status_code
        == 422
    )


def test_import_rejects_large_files(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10)
    response = client.post(
        "/buildings/100000/readings/import",
        headers={"X-API-Key": KEY},
        files={"file": ("x.csv", b"timestamp,kwh\n" * 5, "text/csv")},
    )
    assert response.status_code == 413
