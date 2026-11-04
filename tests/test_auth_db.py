"""Login, roles and tenant isolation against a real database (needs TEST_DATABASE_URL)."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from src.api import auth, main
from src.api.repository import PostgresRepository
from src.common.db import connect
from src.data.customer import TenantInfo, register_tenant
from src.data.load_db import ensure_demo_admin, ensure_role
from src.scoring.service import run_once
from tests.conftest import make_tables
from tests.test_db import count, db, empty_db, load_customer, pytestmark  # noqa: F401

PASSWORD = "test-user-password-1"
H = {"X-Requested-With": "test"}


@pytest.fixture
def api(db, monkeypatch, fresh_settings):  # noqa: F811
    main.app.dependency_overrides[main.get_repository] = lambda: PostgresRepository(db)
    monkeypatch.setattr(main, "throttle", auth.LoginThrottle())
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def add_user(url: str, email: str, role: str, tenant_id: int | None = None) -> None:
    password_hash = auth.hash_password(PASSWORD)
    PostgresRepository(url).create_user(tenant_id, email, None, role, password_hash)


def sign_in(client: TestClient, email: str) -> None:
    body = {"email": email, "password": PASSWORD}
    response = client.post("/auth/login", json=body, headers=H)
    assert response.status_code == 200, response.text
    assert "httponly" in response.headers["set-cookie"].lower()


def test_login_sessions_and_roles(api, db):  # noqa: F811
    add_user(db, "staff@platform.co", "admin")
    add_user(db, "viewer@platform.co", "viewer")
    assert api.get("/buildings").status_code == 401
    wrong = {"email": "staff@platform.co", "password": "not-the-password"}
    assert api.post("/auth/login", json=wrong, headers=H).status_code == 401
    unknown = {**wrong, "email": "nobody@x.co"}
    assert api.post("/auth/login", json=unknown, headers=H).status_code == 401
    assert api.post("/auth/login", json={**wrong, "password": PASSWORD}).status_code == 403

    sign_in(api, "staff@platform.co")
    token = api.cookies[auth.COOKIE]
    # Only the hash of the token is stored.
    assert count(db, "SELECT count(*) FROM sessions WHERE token_hash = %s", (token,)) == 0
    assert (
        count(db, "SELECT count(*) FROM sessions WHERE token_hash = %s", (auth.token_hash(token),))
        == 1
    )
    assert api.get("/auth/me").json()["role"] == "admin"
    assert api.post("/tenants", json={"name": "Acme"}, headers=H).status_code == 201
    assert api.post("/auth/logout", headers=H).status_code == 204
    api.cookies.set(auth.COOKIE, token)
    assert api.get("/auth/me").status_code == 401
    assert count(db, "SELECT count(*) FROM sessions") == 0
    api.cookies.clear()

    sign_in(api, "viewer@platform.co")
    assert api.get("/buildings").status_code == 200
    assert api.post("/tenants", json={"name": "Other"}, headers=H).status_code == 403
    assert api.post("/buildings", json={"external_id": "x"}, headers=H).status_code == 403
    feedback = {"verdict": "confirmed"}
    assert api.post("/anomalies/1/feedback", json=feedback, headers=H).status_code == 403


def test_login_is_throttled(api, db):  # noqa: F811
    add_user(db, "staff@platform.co", "admin")
    wrong = {"email": "staff@platform.co", "password": "not-the-password"}
    for _ in range(5):
        assert api.post("/auth/login", json=wrong, headers=H).status_code == 401
    right = {**wrong, "password": PASSWORD}
    assert api.post("/auth/login", json=right, headers=H).status_code == 429


def test_tenants_see_only_their_own_buildings(api, db, scorer):  # noqa: F811
    with connect(db) as conn:
        acme = register_tenant(conn, TenantInfo("Acme"))
        globex = register_tenant(conn, TenantInfo("Globex"))
    ids = load_customer(db, make_tables(building_ids=(100000, 100001), days=20))
    mine, theirs = ids[100000], ids[100001]
    with connect(db) as conn:
        for building, tenant in ((mine, acme), (theirs, globex)):
            conn.execute(
                "UPDATE buildings SET tenant_id = %s WHERE building_id = %s", (tenant, building)
            )
        conn.execute(
            "INSERT INTO anomalies (building_id, ts_start, ts_end, est_cost_tl, origin) "
            "SELECT building_id, '2026-01-10', '2026-01-10 03:00', 100, 'live' FROM buildings"
        )
    run_once(db, scorer)
    add_user(db, "admin@acme.co", "admin", acme)
    add_user(db, "operator@acme.co", "operator", acme)
    add_user(db, "staff@platform.co", "admin")
    foreign = count(db, "SELECT min(id) FROM anomalies WHERE building_id = %s", (theirs,))

    sign_in(api, "operator@acme.co")
    assert [b["building_id"] for b in api.get("/buildings").json()] == [mine]
    assert {a["building_id"] for a in api.get("/anomalies").json()} == {mine}
    assert api.get("/anomalies", params={"building_id": theirs}).json() == []
    assert api.get("/summary").json()["buildings"] == 1
    assert [t["name"] for t in api.get("/tenants").json()] == ["Acme"]
    for path in ("readings", "forecast", "operating-hours", "downtime"):
        assert api.get(f"/buildings/{theirs}/{path}").status_code == 404
        assert api.get(f"/buildings/{mine}/{path}").status_code == 200
    feedback = {"verdict": "confirmed"}
    assert api.post(f"/anomalies/{foreign}/feedback", json=feedback, headers=H).status_code == 404
    csv = {"file": ("x.csv", b"timestamp,kwh\n2026-02-01 00:00,5\n", "text/csv")}
    response = api.post(f"/buildings/{theirs}/readings/import", files=csv, headers=H)
    assert response.status_code == 404
    period = {"starts_at": "2027-01-01T00:00", "ends_at": "2027-01-02T00:00", "kind": "holiday"}
    assert api.post(f"/buildings/{theirs}/downtime", json=period, headers=H).status_code == 404
    assert api.put(f"/buildings/{theirs}/operating-hours", json=[], headers=H).status_code == 404
    assert api.post("/buildings", json={"external_id": "op-made"}, headers=H).status_code == 403

    sign_in(api, "admin@acme.co")
    # A tenant admin always registers into the own tenant and cannot take another's id.
    body = {"external_id": "acme-new", "tenant_id": globex}
    created = api.post("/buildings", json=body, headers=H)
    assert created.status_code == 201
    query = "SELECT tenant_id FROM buildings WHERE building_id = %s"
    assert count(db, query, (created.json()["id"],)) == acme
    taken = {"external_id": "test-100001"}
    assert api.post("/buildings", json=taken, headers=H).status_code == 409
    user = {"email": "new@acme.co", "role": "viewer", "password": PASSWORD, "tenant_id": globex}
    assert api.post("/users", json=user, headers=H).status_code == 201
    assert api.post("/users", json=user, headers=H).status_code == 409
    assert count(db, "SELECT tenant_id FROM users WHERE email = 'new@acme.co'") == acme
    emails = {u["email"] for u in api.get("/users").json()}
    assert emails == {"admin@acme.co", "operator@acme.co", "new@acme.co"}
    assert api.post("/tenants", json={"name": "Initech"}, headers=H).status_code == 403

    sign_in(api, "staff@platform.co")
    assert {b["building_id"] for b in api.get("/buildings").json()} >= {mine, theirs}
    assert len(api.get("/tenants").json()) == 2


def test_feedback_records_the_signed_in_user(api, db):  # noqa: F811
    ids = load_customer(db, make_tables(building_ids=(100000,), days=20))
    with connect(db) as conn:
        anomaly = conn.execute(
            "INSERT INTO anomalies (building_id, ts_start, ts_end, origin) "
            "VALUES (%s, '2026-01-10', '2026-01-10 03:00', 'live') RETURNING id",
            (ids[100000],),
        ).fetchone()[0]
    add_user(db, "operator@platform.co", "operator")
    sign_in(api, "operator@platform.co")
    body = {"verdict": "false_alarm"}
    assert api.post(f"/anomalies/{anomaly}/feedback", json=body, headers=H).status_code == 201
    query = "SELECT count(*) FROM anomaly_feedback WHERE created_by = %s"
    assert count(db, query, ("operator@platform.co",)) == 1


def test_reader_cannot_see_passwords_or_sessions(db):  # noqa: F811
    reader = f"r_{uuid.uuid4().hex[:8]}"
    password = "test-role-password-1"
    with connect(db) as conn:
        ensure_role(conn, reader, password, read_only=True)
    try:
        with connect(make_conninfo(db, user=reader, password=password)) as conn:
            assert conn.execute("SELECT count(*) FROM buildings").fetchone()[0] == 0
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT password_hash FROM users")
    finally:
        with connect(db, autocommit=True) as conn:
            conn.execute(f"GRANT {reader} TO CURRENT_USER")
            conn.execute(f"DROP OWNED BY {reader}")
            conn.execute(f"DROP ROLE {reader}")


def test_demo_admin_password_follows_env(db, monkeypatch):  # noqa: F811
    monkeypatch.setenv("DEMO_ADMIN_EMAIL", "Admin@Enerji.Local")
    monkeypatch.setenv("DEMO_ADMIN_PASSWORD", PASSWORD)
    with connect(db) as conn:
        assert ensure_demo_admin(conn) == "admin@enerji.local"
        monkeypatch.setenv("DEMO_ADMIN_PASSWORD", "another-password-1")
        ensure_demo_admin(conn)
    found = PostgresRepository(db).login_user("admin@enerji.local")
    assert found is not None and found[0].is_platform and found[0].is_admin
    # A changed .env applies on the next start.
    assert auth.verify_password("another-password-1", found[1])
    monkeypatch.delenv("DEMO_ADMIN_EMAIL")
    with connect(db) as conn:
        assert ensure_demo_admin(conn) is None
