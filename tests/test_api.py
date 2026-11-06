import pytest
from fastapi.testclient import TestClient

from src.api import auth, main
from src.api.repository import DemoRepository
from src.api.schemas import Schedule
from src.config import get_settings

# Writes need this header; a cross-site form cannot set it.
H = {"X-Requested-With": "test"}


@pytest.fixture
def client(monkeypatch, fresh_settings):
    monkeypatch.delenv("DATABASE_URL", raising=False)
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
    assert client.get(f"/buildings/{2**31}/readings").status_code == 422
    assert client.get("/anomalies", params={"building_id": 2**31}).status_code == 422


def test_demo_reads_without_login_and_refuses_writes(client):
    assert client.get("/auth/me").json()["email"] == "demo@localhost"
    body = {"verdict": "confirmed"}
    assert client.post("/anomalies/1/feedback", json=body).status_code == 403
    # Demo mode has nowhere to store writes.
    assert client.post("/anomalies/1/feedback", json=body, headers=H).status_code == 503
    bad = {"verdict": "maybe"}
    assert client.post("/anomalies/1/feedback", json=bad, headers=H).status_code == 422
    login = {"email": "a@b.co", "password": "x"}
    assert client.post("/auth/login", json=login, headers=H).status_code == 503


def test_register_validates_body(client):
    headers = H
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
        headers=H,
        files={"file": ("x.csv", b"timestamp,kwh\n" * 5, "text/csv")},
    )
    assert response.status_code == 413


def test_schedule_reads_in_demo_mode(client):
    hours = client.get("/buildings/100000/operating-hours").json()
    assert [h["weekday"] for h in hours] == [1, 2, 3, 4, 5]
    assert hours[0] == {"weekday": 1, "opens": "08:00:00", "closes": "19:00:00"}
    assert client.get("/buildings/100000/downtime").json() == []
    assert client.get("/buildings/1/operating-hours").status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/tenants", {"name": "Acme"}),
        ("put", "/buildings/100000/operating-hours", []),
        (
            "post",
            "/buildings/100000/downtime",
            {"starts_at": "2026-12-31T00:00", "ends_at": "2027-01-02T00:00", "kind": "holiday"},
        ),
        ("delete", "/buildings/100000/downtime/1", None),
    ],
)
def test_schedule_and_tenant_writes_need_header_and_database(client, method, path, body):
    kwargs = {} if body is None else {"json": body}
    assert client.request(method, path, **kwargs).status_code == 403
    assert client.request(method, path, headers=H, **kwargs).status_code == 503


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/tenants", {"name": "Acme", "tax_number": "12345"}),
        ("post", "/tenants", {"name": "Acme", "status": "trial"}),
        ("post", "/tenants", {"name": ""}),
        ("post", "/buildings", {"external_id": "ok", "segment": "spaceport"}),
        ("post", "/buildings", {"external_id": "ok", "tenant_id": 0}),
        ("post", "/buildings", {"external_id": "ok", "tenant_id": 2**31}),
        ("delete", f"/buildings/100000/downtime/{2**63}", None),
        (
            "post",
            "/buildings/100000/downtime",
            {"starts_at": "2027-01-02T00:00", "ends_at": "2027-01-01T00:00", "kind": "holiday"},
        ),
        (
            "post",
            "/buildings/100000/downtime",
            {
                "starts_at": "2027-01-01T00:00+03:00",
                "ends_at": "2027-01-02T00:00",
                "kind": "holiday",
            },
        ),
        (
            "put",
            "/buildings/100000/operating-hours",
            [{"weekday": 8, "opens": "08:00", "closes": "18:00"}],
        ),
        (
            "put",
            "/buildings/100000/operating-hours",
            [{"weekday": 1, "opens": "08:00", "closes": "12:00"}] * 2,
        ),
        (
            "put",
            "/buildings/100000/operating-hours",
            [
                {"weekday": 1, "opens": "08:00", "closes": "12:00"},
                {"weekday": 1, "opens": "11:00", "closes": "13:00"},
            ],
        ),
        (
            "put",
            "/buildings/100000/operating-hours",
            [
                {"weekday": 7, "opens": "22:00", "closes": "06:00"},
                {"weekday": 1, "opens": "05:00", "closes": "09:00"},
            ],
        ),
    ],
)
def test_schedule_and_tenant_bodies_are_validated(client, method, path, body):
    response = client.request(method, path, json=body, headers=H)
    assert response.status_code == 422


def test_back_to_back_periods_are_allowed():
    shifts = [
        {"weekday": 7, "opens": "22:00", "closes": "06:00"},
        {"weekday": 1, "opens": "06:00", "closes": "14:00"},
        {"weekday": 1, "opens": "14:00", "closes": "22:00"},
        {"weekday": 3, "opens": "00:00", "closes": "00:00"},
    ]
    assert len(Schedule.model_validate(shifts).root) == 4


def test_passwords_hash_and_verify():
    stored = auth.hash_password("correct horse battery")
    assert stored.startswith("scrypt$") and "correct" not in stored
    assert auth.verify_password("correct horse battery", stored)
    assert not auth.verify_password("wrong horse battery", stored)
    assert not auth.verify_password("anything", None)
    assert not auth.verify_password("anything", "garbage")
    with pytest.raises(ValueError):
        auth.hash_password("short")


def test_login_throttle_blocks_after_repeated_failures():
    throttle = auth.LoginThrottle(limit=2, window=60)
    throttle.failed("a@b.co")
    assert not throttle.blocked("a@b.co")
    throttle.failed("a@b.co")
    assert throttle.blocked("a@b.co") and not throttle.blocked("c@d.co")
    throttle.succeeded("a@b.co")
    assert not throttle.blocked("a@b.co")


def test_buildings_carry_a_tariff_and_windows_can_end_earlier(client):
    default = get_settings().tariff_tl_per_kwh
    assert {b["tariff_tl_per_kwh"] for b in client.get("/buildings").json()} == {default}
    params = {"hours": 24, "end": "2026-10-19T05:00:00"}
    readings = client.get("/buildings/100000/readings", params=params).json()
    forecast = client.get("/buildings/100000/forecast", params=params).json()
    assert len(readings) == len(forecast) == 24
    assert readings[-1]["ts"] == forecast[-1]["ts"] == "2026-10-19T05:00:00"
    with_offset = {"hours": 24, "end": "2026-10-19T05:00:00+03:00"}
    assert client.get("/buildings/100000/readings", params=with_offset).status_code == 422


def test_anomalies_carry_a_recommendation_and_can_be_filtered(client):
    item = client.get("/anomalies").json()[0]
    assert item["recommendation"]["title"] == "Gece yüksek tüketim"
    assert item["recommendation"]["action"]
    for params, found in [
        ({"category": "waste", "status": "open", "order": "cost"}, 1),
        ({"category": "data_issue"}, 0),
        ({"status": "applied"}, 0),
        ({"since": "2026-10-19T06:00:00"}, 0),
        ({"min_cost_tl": 1000}, 0),
    ]:
        assert len(client.get("/anomalies", params=params).json()) == found, params
    for bad in [{"status": "done"}, {"equipment_type": "Klima"}, {"min_cost_tl": -1}]:
        assert client.get("/anomalies", params=bad).status_code == 422, bad


def test_recommendation_table_and_tenant_summaries(client):
    rows = client.get("/recommendations").json()
    assert {"klima", "*"} <= {r["equipment_type"] for r in rows}
    assert client.get("/tenants/summary").json() == []
    assert client.get("/tenants/1/summary").status_code == 404
    assert client.get("/tenants/summary", params={"days": 0}).status_code == 422


def test_recommendation_status_is_a_verdict(client):
    for verdict in ("sent", "applied"):
        body = {"verdict": verdict}
        assert client.post("/anomalies/1/feedback", json=body, headers=H).status_code == 503
