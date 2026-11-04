from __future__ import annotations

import os
import uuid

import pandas as pd
import pytest
from psycopg.conninfo import make_conninfo

from src.api.auth import verify_password
from src.common.db import connect, normalize_url
from src.data.catalog import (
    CATALOG_PATH,
    catalog_problems,
    demo_email,
    load_catalog,
    read_catalog,
)
from src.data.customer import BuildingInfo, register_building
from src.data.load_db import apply_schema

TEST_URL = os.getenv("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL is not set")

ROWS = [
    ("cat-a", "Otel", "uzman", "hotel", "Mutfak", "Soğuk oda", "sogutma"),
    ("cat-b", "Otel", "uzman", "hotel", "Lobi", "Lobi aydınlatma", "aydinlatma"),
    ("cat-c", "Özel Kolej", "izleme", "education", "Ana Bina", "Ana bina", "genel"),
]


def small_catalog() -> pd.DataFrame:
    columns = ["external_id", "tenant", "plan", "segment", "location", "asset_name"]
    return pd.DataFrame(ROWS, columns=[*columns, "equipment_type"])


def test_committed_catalog_is_valid():
    frame = read_catalog(CATALOG_PATH)
    assert catalog_problems(frame) == []
    assert len(frame) == 30
    assert frame.groupby("tenant").size().eq(6).all()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda f: f.drop(columns="plan"), "missing columns: plan"),
        (lambda f: f.assign(equipment_type="Klima"), "unknown equipment_type: Klima"),
        (lambda f: f.assign(segment="hostel"), "unknown segment: hostel"),
        (lambda f: f.assign(external_id="cat-a"), "duplicate external_id: cat-a"),
        (lambda f: f.assign(location=""), "empty location values"),
        (lambda f: f.assign(plan=["uzman", "analiz", "izleme"]), "'Otel' has more than one plan"),
        (lambda f: f.assign(tariff_tl_per_kwh=-1.0), "negative tariff_tl_per_kwh"),
        (lambda f: f.assign(location="Mutfak"), "'Otel' repeats location 'Mutfak'"),
    ],
)
def test_catalog_problems_are_reported(change, message):
    assert any(message in p for p in catalog_problems(change(small_catalog())))


def test_demo_email_is_plain_ascii():
    assert demo_email("Üniversite Kampüsü") == "universitekampusu@enerji.local"
    assert demo_email("Özel Kolej") == "ozelkolej@enerji.local"
    assert demo_email("Ofis Plazası") == "ofisplazasi@enerji.local"


@pytest.fixture
def db() -> str:
    name = f"t_{uuid.uuid4().hex[:12]}"
    with connect(TEST_URL, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {name}")
    url = make_conninfo(normalize_url(TEST_URL), dbname=name)
    try:
        with connect(url) as conn:
            apply_schema(conn)
            for external_id, *_ in ROWS:
                register_building(conn, BuildingInfo(external_id))
        yield url
    finally:
        with connect(TEST_URL, autocommit=True) as conn:
            conn.execute(f"DROP DATABASE {name} WITH (FORCE)")


@needs_db
def test_catalog_load_is_idempotent(db):
    frame = small_catalog()
    with connect(db) as conn:
        load_catalog(conn, frame, "demo-tenant-password")
        written = load_catalog(conn, frame.assign(tariff_tl_per_kwh=4.2), "another-password-1")
    with connect(db) as conn:
        tenants = dict(conn.execute("SELECT name, plan FROM tenants").fetchall())
        buildings = conn.execute(
            "SELECT b.external_id, t.name, b.name, b.segment, b.tariff_tl_per_kwh, "
            "a.kind, a.equipment_type FROM buildings b JOIN tenants t USING (tenant_id) "
            "JOIN meters m ON m.building_id = b.building_id AND m.is_main "
            "JOIN assets a ON a.asset_id = m.asset_id ORDER BY 1"
        ).fetchall()
        assets = conn.execute("SELECT count(*) FROM assets").fetchone()[0]
        users = {
            email: (role, password_hash)
            for email, role, password_hash in conn.execute(
                "SELECT email, role, password_hash FROM users"
            ).fetchall()
        }
    assert written == {"tenants": 2, "buildings": 3, "users": 2}
    assert tenants == {"Otel": "uzman", "Özel Kolej": "izleme"}
    assert buildings == [
        ("cat-a", "Otel", "Mutfak", "hotel", 4.2, "machine", "sogutma"),
        ("cat-b", "Otel", "Lobi", "hotel", 4.2, "machine", "aydinlatma"),
        ("cat-c", "Özel Kolej", "Ana Bina", "education", 4.2, "zone", "genel"),
    ]
    assert assets == 3
    assert users["otel@enerji.local"][0] == "operator"
    assert users["ozelkolej@enerji.local"][0] == "viewer"
    # The second run's password applies.
    assert verify_password("another-password-1", users["otel@enerji.local"][1])


@needs_db
def test_catalog_with_unknown_building_writes_nothing(db):
    frame = pd.concat(
        [small_catalog(), small_catalog().iloc[[0]].assign(external_id="cat-x", location="Bahçe")]
    )
    with connect(db) as conn, pytest.raises(LookupError, match="cat-x"):
        load_catalog(conn, frame)
    with connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM tenants").fetchone()[0] == 0
