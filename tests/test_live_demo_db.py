"""Live demo against a real database (needs TEST_DATABASE_URL)."""

from __future__ import annotations

import pandas as pd

from src.api.repository import PostgresRepository
from src.common.db import connect
from src.ingest import live_demo as ld
from tests.conftest import make_tables
from tests.test_auth_db import PASSWORD, H, add_user, api, sign_in  # noqa: F401
from tests.test_db import count, db, empty_db, load_customer, pytestmark  # noqa: F401

METERS = pd.DataFrame(
    {
        "external_id": ["test-100000", "test-100001"],
        "location": ["Üretim Holü", "Soğuk Depo"],
        "asset_name": ["Hol aydınlatması", "Soğuk oda"],
        "equipment_type": ["aydinlatma", "sogutma"],
        "scenario": ["night_base_load", "stuck"],
    }
)


def test_register_is_repeatable_and_reset_clears_a_run(db):  # noqa: F811
    lead = load_customer(db, make_tables(days=10))
    with connect(db) as conn:
        live = ld.register(conn, METERS, PASSWORD)
        assert ld.register(conn, METERS, PASSWORD) == live
        tenant = conn.execute("SELECT tenant_id FROM live_demo").fetchone()[0]
    assert set(live) == set(lead.values()) and not set(live.values()) & set(lead.values())
    repo = PostgresRepository(db)
    state = repo.live_demo()
    assert (state["running"], state["available"], state["tenant_id"]) == (False, False, tenant)
    assert [m["equipment_type"] for m in state["meters"]] == ["sogutma", "aydinlatma"]
    assert count(db, "SELECT count(*) FROM users WHERE tenant_id = %s", (tenant,)) == 1

    repo.set_live_demo(True)
    repo.set_live_demo(True)
    with connect(db) as conn:
        assert ld.read_state(conn) == (True, 2)
        ld.beat(conn, pd.Timestamp("2026-11-01 02:00"))
        building = next(iter(live.values()))
        conn.execute(
            "INSERT INTO readings (building_id, ts, kwh) VALUES (%s, '2026-11-01 02:00', 5)",
            (building,),
        )
    state = repo.live_demo()
    assert state["available"] and state["sim_ts"] == pd.Timestamp("2026-11-01 02:00")
    meter = next(m for m in state["meters"] if m["building_id"] == building)
    assert meter["last_kwh"] == 5
    with connect(db) as conn:
        ld.reset(conn, list(live.values()))
    assert count(db, "SELECT count(*) FROM readings WHERE building_id = %s", (building,)) == 0
    repo.set_live_demo(False)
    assert not repo.live_demo()["running"]


def test_only_platform_admins_start_the_live_demo(api, db):  # noqa: F811
    load_customer(db, make_tables(days=10))
    with connect(db) as conn:
        ld.register(conn, METERS, None)
        tenant = conn.execute("SELECT tenant_id FROM live_demo").fetchone()[0]
    add_user(db, "staff@platform.co", "admin")
    add_user(db, "boss@canli.co", "admin", tenant)
    sign_in(api, "boss@canli.co")
    assert len(api.get("/live-demo").json()["meters"]) == 2
    assert api.post("/live-demo/start", headers=H).status_code == 403
    sign_in(api, "staff@platform.co")
    assert api.post("/live-demo/start").status_code == 403  # writes need the header
    assert api.post("/live-demo/start", headers=H).json()["running"] is True
    assert api.post("/live-demo/stop", headers=H).json()["running"] is False
