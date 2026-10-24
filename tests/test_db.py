"""Postgres integration tests. They run only when TEST_DATABASE_URL points at a disposable database.

Each test gets its own database, created next to the test database and dropped afterwards.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import replace
from datetime import datetime, time

import pandas as pd
import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from src.api.repository import ConflictError, NotFoundError, PostgresRepository
from src.common.db import connect, normalize_url
from src.data import weather_api
from src.data.customer import (
    BuildingInfo,
    TenantInfo,
    backfill_weather,
    import_file,
    register_building,
    register_tenant,
    upsert_readings,
    upsert_weather,
)
from src.data.dataset import model_frame
from src.data.feedback import apply_feedback, fetch_feedback
from src.data.load_db import apply_schema, ensure_role, ensure_roles_from_env, load_tables
from src.data.store import fetch_tables
from src.ingest.mqtt_ingest import Buffer, Reading
from src.models import registry
from src.scoring.service import refresh_scorer, run_once
from tests.conftest import daily_profile, make_tables

TEST_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL is not set")


@pytest.fixture
def empty_db() -> str:
    name = f"t_{uuid.uuid4().hex[:12]}"
    with connect(TEST_URL, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {name}")
    try:
        yield make_conninfo(normalize_url(TEST_URL), dbname=name)
    finally:
        with connect(TEST_URL, autocommit=True) as conn:
            conn.execute(f"DROP DATABASE {name} WITH (FORCE)")


@pytest.fixture
def db(empty_db) -> str:
    with connect(empty_db) as conn:
        apply_schema(conn)
    return empty_db


def load_customer(url: str, tables) -> dict[int, int]:
    """Onboard synthetic buildings the way a customer would; returns old id -> database id."""
    ids = {}
    with connect(url) as conn:
        for b in tables.buildings.itertuples():
            ids[b.building_id] = register_building(
                conn,
                BuildingInfo(
                    external_id=b.external_id,
                    lat=b.lat,
                    lng=b.lng,
                    timezone=b.timezone,
                    primary_use=b.primary_use,
                    square_feet=b.square_feet,
                ),
            )
        upsert_weather(conn, tables.weather, "open-meteo")
        for old, new in ids.items():
            rows = tables.readings[tables.readings.building_id == old]
            upsert_readings(conn, new, rows.assign(quality=None), "csv")
    return ids


def count(url: str, query: str, params: tuple = ()) -> int:
    with connect(url) as conn:
        return conn.execute(query, params).fetchone()[0]


def test_schema_can_be_applied_again(db):
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO buildings (external_id, weather_key, primary_use) "
            "VALUES ('old', 'site', 'Office')"
        )
        apply_schema(conn)
    assert count(db, "SELECT count(*) FROM buildings WHERE segment = 'office'") == 1


def test_connections_find_tables_without_database_settings(db, monkeypatch):
    with connect(db, autocommit=True) as conn:
        conn.execute(f"ALTER DATABASE {conn.info.dbname} RESET search_path")
    assert count(db, "SELECT count(*) FROM buildings") == 0
    monkeypatch.setenv("PGOPTIONS", "-c lock_timeout=2000")
    with connect(make_conninfo(db, options="-c statement_timeout=1234")) as conn:
        assert conn.execute("SHOW statement_timeout").fetchone()[0] == "1234ms"
    with connect(db) as conn:
        assert conn.execute("SHOW lock_timeout").fetchone()[0] == "2s"
        assert conn.execute("SELECT count(*) FROM buildings").fetchone()[0] == 0


def test_schema_moves_tables_out_of_public(empty_db):
    with connect(empty_db) as conn:
        conn.execute("CREATE TABLE public.tenants (tenant_id INT PRIMARY KEY, name TEXT UNIQUE)")
        conn.execute("INSERT INTO public.tenants VALUES (7, 'Acme')")
        conn.execute("CREATE VIEW public.daily_kpis AS SELECT * FROM public.tenants")
    with connect(empty_db) as conn:
        apply_schema(conn)
        left = conn.execute(
            "SELECT (SELECT count(*) FROM pg_tables WHERE schemaname = 'public') "
            "+ (SELECT count(*) FROM pg_views WHERE schemaname = 'public')"
        ).fetchone()[0]
        row = conn.execute("SELECT tenant_id, name, status FROM core.tenants").fetchone()
    assert left == 0
    assert row == (7, "Acme", "pilot")


def test_lead_load_is_idempotent_and_reads_back_identically(db):
    tables = make_tables()
    lead = replace(tables, buildings=tables.buildings.assign(source="lead"))
    with connect(db) as conn:
        assert load_tables(conn, lead)
        assert not load_tables(conn, lead)
        building = int(tables.buildings.building_id.iloc[0])
        asset = conn.execute(
            "INSERT INTO assets (building_id, kind, name) VALUES (%s, 'line', 'L1') "
            "RETURNING asset_id",
            (building,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO meters (building_id, asset_id, external_id) VALUES (%s, %s, 'm-1')",
            (building, asset),
        )
        conn.execute("INSERT INTO alert_settings (building_id) VALUES (%s)", (building,))
        conn.execute(
            "INSERT INTO work_orders (building_id, title) VALUES (%s, 'check')", (building,)
        )
        assert load_tables(conn, lead, force=True)
        back = fetch_tables(conn)
    assert count(db, "SELECT count(*) FROM readings") == len(tables.readings)
    assert count(db, "SELECT count(*) FROM assets") == 0
    assert count(db, "SELECT count(*) FROM buildings WHERE segment = 'office'") == len(
        tables.buildings
    )
    expected, actual = model_frame(lead), model_frame(back)
    pd.testing.assert_frame_equal(
        expected[actual.columns].reset_index(drop=True),
        actual.reset_index(drop=True),
        check_dtype=False,
    )


def test_register_assigns_customer_ids_and_updates_in_place(db):
    with connect(db) as conn:
        first = register_building(conn, BuildingInfo("acme-hq", lat=41.0, lng=29.0, country="cy"))
        update = BuildingInfo("acme-hq", name="Acme HQ", tariff_tl_per_kwh=4.2)
        again = register_building(conn, update)
        row = conn.execute(
            "SELECT name, country, weather_key, tariff_tl_per_kwh FROM buildings "
            "WHERE building_id = %s",
            (first,),
        ).fetchone()
    assert first == again >= 100000
    assert row == ("Acme HQ", "CY", "geo:41.00,29.00", 4.2)


def fake_weather(loc, start, end) -> pd.DataFrame:
    ts = pd.date_range(pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(hours=23), freq="h")
    return pd.DataFrame(
        {
            "weather_key": loc.weather_key,
            "ts": ts,
            "air_temp": 12.5,
            "dew_temp": 5.0,
            "wind_speed": 3.0,
            "cloud_cover": 4.0,
            "precip_1h": 0.0,
            "sea_level_pressure": 1013.0,
        }
    )


def test_import_is_idempotent_and_backfills_weather(db, monkeypatch):
    calls = []

    def fake_fetch(loc, start, end):
        calls.append((start, end))
        return fake_weather(loc, start, end)

    monkeypatch.setattr(weather_api, "fetch", fake_fetch)
    raw = pd.DataFrame(
        {"ts": pd.date_range("2026-03-02", periods=48, freq="h").astype(str), "value": 10.0}
    )
    with connect(db) as conn:
        building_id = register_building(conn, BuildingInfo("acme-hq"))
        report = import_file(conn, "acme-hq", raw)
        import_file(conn, "acme-hq", raw.assign(value=11.0))
        assert backfill_weather(conn, building_id) == 0
    assert report.hours_out == 48 and len(calls) == 1
    assert count(db, "SELECT count(*) FROM readings WHERE kwh = 11") == 48
    assert count(db, "SELECT count(*) FROM readings WHERE air_temp = 12.5") == 48


def test_mqtt_buffer_writes_registered_buildings_only(db):
    with connect(db) as conn:
        register_building(conn, BuildingInfo("acme-hq"))
    buffer = Buffer(db)
    for ts in pd.date_range("2026-03-02", periods=3, freq="h"):
        buffer.add(Reading("acme-hq", ts, 5.0))
    buffer.add(Reading("unknown", pd.Timestamp("2026-03-02"), 5.0))
    assert buffer.flush() == 3 and buffer.flush() == 0
    assert count(db, "SELECT count(*) FROM readings WHERE source = 'mqtt'") == 3


def test_live_scoring_writes_once_and_picks_up_new_hours(db, scorer):
    tables = make_tables()
    night = (
        (tables.readings.building_id == 100000)
        & (tables.readings.ts.dt.date == pd.Timestamp("2026-02-05").date())
        & tables.readings.ts.dt.hour.isin(range(6))
    )
    tables.readings.loc[night, "kwh"] *= 3
    ids = load_customer(db, tables)
    hours = len(tables.readings)

    assert run_once(db, scorer) == hours
    assert run_once(db, scorer) == 0
    assert count(db, "SELECT count(*) FROM predictions WHERE origin = 'live'") == hours
    waste = count(
        db,
        "SELECT count(*) FROM anomalies WHERE building_id = %s "
        "AND rule_type = 'night_base_load' AND ts_start = '2026-02-05 00:00'",
        (ids[100000],),
    )
    assert waste == 1

    events = count(db, "SELECT count(*) FROM anomalies")
    ts = pd.date_range(tables.readings.ts.max() + pd.Timedelta(hours=1), periods=24, freq="h")
    with connect(db) as conn:
        upsert_readings(
            conn,
            ids[100000],
            pd.DataFrame(
                {"ts": ts, "kwh": daily_profile(ts, 50.0), "meter_raw": None, "quality": None}
            ),
            "mqtt",
        )
    assert run_once(db, scorer, weather=fake_weather) == 24
    new_hours = "SELECT count(*) FROM readings WHERE air_temp = 12.5 AND ts >= %s"
    assert count(db, new_hours, (ts[0].to_pydatetime(),)) == 24
    assert count(
        db, "SELECT max(last_scored_ts) = %s FROM scoring_state", (ts[-1].to_pydatetime(),)
    )
    assert count(db, "SELECT count(*) FROM anomalies") >= events
    assert (
        count(db, "SELECT count(*) - count(DISTINCT (building_id, ts_start)) FROM anomalies") == 0
    )


def test_event_spanning_two_cycles_stays_one_event(db, scorer):
    tables = make_tables()
    r = tables.readings
    night = r.ts.between("2026-02-05 00:00", "2026-02-05 05:00")
    r.loc[night & (r.building_id == 100000), "kwh"] *= 3
    first = r.ts <= "2026-02-05 02:00"
    ids = load_customer(db, replace(tables, readings=r[first]))
    run_once(db, scorer)
    with connect(db) as conn:
        for old, new in ids.items():
            rest = r[~first & (r.building_id == old)]
            upsert_readings(conn, new, rest.assign(quality=None), "mqtt")
    run_once(db, scorer)

    with connect(db) as conn:
        rows = conn.execute(
            "SELECT ts_start, ts_end FROM anomalies WHERE building_id = %s "
            "AND ts_end >= '2026-02-05 00:00' AND ts_start <= '2026-02-05 05:00'",
            (ids[100000],),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == pd.Timestamp("2026-02-05 00:00")
    assert rows[0][1] >= pd.Timestamp("2026-02-05 05:00")


def test_event_longer_than_the_context_stays_one_row(db, scorer):
    tables = make_tables(building_ids=(100000,), days=80)
    r = tables.readings
    stuck = r.ts >= "2026-01-26"
    r.loc[stuck, "kwh"] = 40.0
    first = r.ts <= "2026-03-20"
    ids = load_customer(db, replace(tables, readings=r[first]))
    run_once(db, scorer)
    with connect(db) as conn:
        upsert_readings(conn, ids[100000], r[~first].assign(quality=None), "mqtt")
    run_once(db, scorer)

    with connect(db) as conn:
        rows = conn.execute(
            "SELECT ts_start, ts_end FROM anomalies WHERE ts_end >= '2026-03-01'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] < pd.Timestamp("2026-01-27") and rows[0][1] == r.ts.max()


def test_incremental_live_scoring_matches_a_full_rescore(db, scorer):
    tables = make_tables(days=50)
    r = tables.readings
    night = r.ts.between("2026-02-20 00:00", "2026-02-20 05:00") & (r.building_id == 100000)
    r.loc[night, "kwh"] *= 3
    # The cycle ends on the first waste hour, which only the next hour can confirm.
    first = r.ts <= "2026-02-20 00:00"
    ids = load_customer(db, replace(tables, readings=r[first]))
    run_once(db, scorer)
    with connect(db) as conn:
        for old, new in ids.items():
            upsert_readings(
                conn, new, r[~first & (r.building_id == old)].assign(quality=None), "mqtt"
            )
    run_once(db, scorer)

    with connect(db) as conn:
        expected = scorer.score(model_frame(fetch_tables(conn)))
        live = conn.execute(
            "SELECT building_id, ts, source FROM predictions ORDER BY building_id, ts"
        ).fetchall()
    expected = expected.sort_values(["building_id", "ts"], ignore_index=True)
    assert [row[2] or "" for row in live] == expected["source"].fillna("").tolist()
    assert (ids[100000], pd.Timestamp("2026-02-20 00:00"), "rule") in live


def test_repository_reads_writes_and_feedback(db, scorer):
    tables = make_tables()
    ids = load_customer(db, tables)
    run_once(db, scorer)
    repo = PostgresRepository(db)
    building = ids[100000]
    end = tables.readings.ts.max()
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO predictions (building_id, ts, origin, expected_kwh, alarm, "
            "model_version) VALUES (%s, %s, 'batch', -1, false, 'old')",
            (building, end),
        )
        anomaly = conn.execute(
            "INSERT INTO anomalies (building_id, ts_start, ts_end, est_cost_tl, source) "
            "VALUES (%s, '2026-02-01 01:00', '2026-02-01 03:00', 100, 'rule') RETURNING id",
            (building,),
        ).fetchone()[0]
        stale = conn.execute(
            "INSERT INTO anomalies (building_id, ts_start, ts_end, est_cost_tl, origin) "
            "VALUES (%s, '2026-02-01 01:00', '2026-02-01 03:00', 100, 'batch') RETURNING id",
            (building,),
        ).fetchone()[0]

    assert {b["building_id"] for b in repo.buildings()} == set(ids.values())
    readings = repo.readings(building, 48)
    assert len(readings) == 48 and readings[-1]["ts"] == end
    forecast = repo.forecast(building, 48)
    assert len(forecast) == 48 and forecast[-1]["origin"] == "live"
    assert repo.summary()["total_cost_tl"] >= 100
    assert stale not in {a["id"] for a in repo.anomalies(building, 1000)}

    repo.add_feedback(anomaly, "confirmed", "valve left open", "ops")
    assert (
        next(a for a in repo.anomalies(building, 50) if a["id"] == anomaly)["verdict"]
        == "confirmed"
    )
    with connect(db) as conn:
        feedback = fetch_feedback(conn)
        labeled = apply_feedback(fetch_tables(conn, [building]).readings, feedback)
    window = labeled.ts.between("2026-02-01 01:00", "2026-02-01 03:00")
    assert (
        labeled.loc[window, "anomaly"].eq(1).all() and labeled.loc[~window, "anomaly"].eq(0).all()
    )
    with connect(db) as conn:
        conn.execute("DELETE FROM anomalies WHERE id = %s", (anomaly,))
        republished = conn.execute(
            "INSERT INTO anomalies (building_id, ts_start, ts_end, source) "
            "VALUES (%s, '2026-02-01 01:00', '2026-02-01 04:00', 'rule') RETURNING id",
            (building,),
        ).fetchone()[0]
    assert (
        next(a for a in repo.anomalies(building, 50) if a["id"] == republished)["verdict"]
        == "confirmed"
    )

    with pytest.raises(NotFoundError):
        repo.add_feedback(10**9, "confirmed", None, None)
    with pytest.raises(NotFoundError):
        repo.readings(10**9, 24)
    with pytest.raises(NotFoundError):
        repo.import_readings(10**9, pd.DataFrame({"ts": [], "value": []}), "kwh", False, None)


def test_tenant_asset_and_work_order_tables(db):
    with connect(db) as conn:
        tenant = conn.execute(
            "INSERT INTO tenants (name) VALUES ('Acme') RETURNING tenant_id"
        ).fetchone()[0]
        user = conn.execute(
            "INSERT INTO users (tenant_id, email, role) VALUES (%s, 'ops@acme.test', 'operator') "
            "RETURNING user_id",
            (tenant,),
        ).fetchone()[0]
        building = register_building(conn, BuildingInfo("acme-plant"))
        conn.execute("UPDATE buildings SET tenant_id = %s", (tenant,))
        line = conn.execute(
            "INSERT INTO assets (building_id, kind, name) VALUES (%s, 'line', 'Line 1') "
            "RETURNING asset_id",
            (building,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO assets (building_id, parent_id, kind, name) "
            "VALUES (%s, %s, 'machine', 'Press 3')",
            (building, line),
        )
        meter = "INSERT INTO meters (building_id, external_id, is_main) VALUES (%s, %s, true)"
        conn.execute(meter, (building, "acme-plant-main"))
        conn.execute(
            "INSERT INTO work_orders (building_id, title, assignee_id, status, resolved_at) "
            "VALUES (%s, 'Compressor left on at night', %s, 'resolved', now())",
            (building, user),
        )
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            conn.execute(meter, (building, "acme-plant-second"))
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO work_orders (building_id, title, status) VALUES (%s, 'x', 'resolved')",
                (building,),
            )
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("INSERT INTO users (email, role) VALUES ('Ops@Acme.test', 'operator')")
    assert count(db, "SELECT count(*) FROM assets WHERE parent_id IS NOT NULL") == 1


def test_tenants_segments_and_schedule(db):
    acme = TenantInfo("Acme", tax_number="1234567890", city="Istanbul")
    with connect(db) as conn:
        tenant = register_tenant(conn, acme)
        assert register_tenant(conn, TenantInfo("Acme", status="active")) == tenant
        tenant_row = conn.execute(
            "SELECT tax_number, city, status FROM tenants WHERE tenant_id = %s", (tenant,)
        ).fetchone()
        office = register_building(conn, BuildingInfo("acme-hq", primary_use="Office"))
        mall = register_building(
            conn, BuildingInfo("acme-mall", tenant_id=tenant, primary_use="Retail", segment="mall")
        )
        register_building(conn, BuildingInfo("acme-mall", primary_use="Office"))
        segments = dict(conn.execute("SELECT external_id, segment FROM buildings").fetchall())
        mall_tenant = conn.execute(
            "SELECT tenant_id FROM buildings WHERE building_id = %s", (mall,)
        ).fetchone()[0]
    assert tenant_row == ("1234567890", "Istanbul", "active")
    assert segments == {"acme-hq": "office", "acme-mall": "mall"} and mall_tenant == tenant

    repo = PostgresRepository(db)
    with pytest.raises(ConflictError):
        repo.register_tenant(replace(acme, name="Other"))
    with pytest.raises(NotFoundError):
        repo.register(BuildingInfo("orphan", tenant_id=10**6))
    assert next(b for b in repo.buildings() if b["building_id"] == office)["segment"] == "office"

    shifts = [
        {"weekday": 1, "opens": time(22), "closes": time(6)},
        {"weekday": 1, "opens": time(6), "closes": time(14)},
    ]
    assert [h["opens"] for h in repo.set_operating_hours(office, shifts)] == [time(6), time(22)]
    assert repo.set_operating_hours(office, shifts[:1]) == shifts[:1]
    period = {
        "starts_at": datetime(2026, 12, 31),
        "ends_at": datetime(2027, 1, 2),
        "kind": "holiday",
        "note": None,
    }
    downtime = repo.add_downtime(office, period)
    assert repo.downtime(office) == [{"id": downtime, "building_id": office, **period}]
    with pytest.raises(NotFoundError):
        repo.delete_downtime(mall, downtime)
    repo.delete_downtime(office, downtime)
    assert repo.downtime(office) == []
    for call in (repo.operating_hours, repo.downtime):
        with pytest.raises(NotFoundError):
            call(10**9)


def test_daily_kpis_count_excess_in_alarm_hours(db, scorer):
    tables = make_tables(building_ids=(100000,))
    load_customer(db, tables)
    run_once(db, scorer)
    with connect(db) as conn:
        days, kwh, alarm_hours, excess = conn.execute(
            "SELECT count(*), sum(kwh), sum(alarm_hours), "
            "count(*) FILTER (WHERE excess_kwh < 0) FROM daily_kpis"
        ).fetchone()
    assert days == 35 and kwh == pytest.approx(tables.readings.kwh.sum(), rel=1e-4)
    assert alarm_hours == count(db, "SELECT count(*) FROM predictions WHERE alarm")
    assert excess == 0
    with connect(db) as conn:
        conn.execute(
            "UPDATE readings SET quality = 'merged' WHERE ts = (SELECT min(ts) FROM readings)"
        )
        missing, merged_kwh = conn.execute(
            "SELECT sum(missing_hours), sum(kwh) FROM daily_kpis"
        ).fetchone()
    assert missing == 1 and merged_kwh < kwh


def test_registry_publishes_and_fetches_latest(db, scorer, tmp_path):
    with pytest.raises(LookupError):
        registry.fetch_latest(db)
    assert registry.latest_version(db) is None
    version = registry.publish(registry.save(scorer, tmp_path), db)
    assert registry.fetch_latest(db).version == version == scorer.version

    newer = replace(scorer, version="test-2")
    assert refresh_scorer(db, scorer) is scorer
    registry.publish(registry.save(newer, tmp_path / "v2"), db)
    assert registry.latest_version(db) == "test-2"
    current = refresh_scorer(db, scorer)
    assert current.version == "test-2"
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO model_artifacts (name, version, meta, expected_model, detector_model) "
            "SELECT name, 'test-3', meta, 'broken', 'broken' FROM model_artifacts "
            "WHERE version = 'test-2'"
        )
    assert refresh_scorer(db, current) is current


def test_roles_get_the_right_rights(db, monkeypatch):
    reader, team = f"r_{uuid.uuid4().hex[:8]}", f"w_{uuid.uuid4().hex[:8]}"
    password = "test-role-password-1"
    with connect(db) as conn:
        with pytest.raises(ValueError):
            ensure_role(conn, reader, "short", read_only=True)
        ensure_role(conn, reader, password, read_only=True)
        ensure_role(conn, team, password, read_only=False)
        register_building(conn, BuildingInfo("acme-hq"))
    try:
        with connect(make_conninfo(db, user=reader, password=password)) as conn:
            assert conn.execute("SELECT count(*) FROM buildings").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM ml.readings_enriched").fetchone()[0] == 0
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("INSERT INTO buildings (external_id, weather_key) VALUES ('x', 'y')")
        with connect(make_conninfo(db, user=team, password=password)) as conn:
            register_building(conn, BuildingInfo("team-made"))
            conn.execute(
                "INSERT INTO planned_downtime (building_id, starts_at, ends_at, kind) "
                "SELECT building_id, '2027-01-01', '2027-01-02', 'holiday' FROM buildings"
            )
        assert count(db, "SELECT count(*) FROM buildings") == 2
        # A run without passwords still restores rights an existing role lost.
        with connect(db) as conn:
            conn.execute(f"REVOKE USAGE ON SCHEMA core FROM {reader}")
            monkeypatch.setenv("DB_READER_USER", reader)
            monkeypatch.delenv("DB_READER_PASSWORD", raising=False)
            monkeypatch.setenv("DB_TEAM_USER", f"absent_{uuid.uuid4().hex[:8]}")
            monkeypatch.delenv("DB_TEAM_PASSWORD", raising=False)
            assert ensure_roles_from_env(conn) == [reader]
        with connect(make_conninfo(db, user=reader, password=password)) as conn:
            assert conn.execute("SELECT count(*) FROM buildings").fetchone()[0] == 2
    finally:
        with connect(db, autocommit=True) as conn:
            for role in (reader, team):
                conn.execute(f"GRANT {role} TO CURRENT_USER")
                conn.execute(f"DROP OWNED BY {role}")
                conn.execute(f"DROP ROLE {role}")


def test_meter_stuck_for_weeks_with_nightly_breaks_stays_one_data_issue(db, scorer):
    # Stuck every day from 05:00, longer than the scoring context, and still going.
    tables = make_tables(building_ids=(100000,), days=80)
    r = tables.readings
    stuck = (r.ts >= "2026-01-26") & (r.ts.dt.hour >= 5)
    r.loc[stuck, "kwh"] = 40.0
    first = r.ts <= "2026-03-20 03:00"
    ids = load_customer(db, replace(tables, readings=r[first]))
    run_once(db, scorer)
    with connect(db) as conn:
        upsert_readings(conn, ids[100000], r[~first].assign(quality=None), "mqtt")
    run_once(db, scorer)

    with connect(db) as conn:
        rows = conn.execute(
            "SELECT ts_start, ts_end, category, est_cost_tl FROM anomalies "
            "WHERE rule_type = 'flatline' AND ts_end >= '2026-03-01'"
        ).fetchall()
    assert len(rows) == 1
    start, end, category, cost = rows[0]
    assert category == "data_issue" and cost is None
    assert start < pd.Timestamp("2026-01-27") and end.date() == r.ts.max().date()


def test_readings_window_can_end_before_the_latest_reading(db):
    building = load_customer(db, make_tables(building_ids=(100000,), days=20))[100000]
    repo = PostgresRepository(db)
    end = datetime(2026, 1, 10, 5)
    rows = repo.readings(building, 24, end)
    assert len(rows) == 24 and rows[0]["ts"] == datetime(2026, 1, 9, 6) and rows[-1]["ts"] == end
    assert repo.readings(building, 24)[-1]["ts"] == datetime(2026, 1, 24, 23)
    with connect(db) as conn:
        conn.execute(
            "UPDATE buildings SET tariff_tl_per_kwh = 4.2 WHERE building_id = %s", (building,)
        )
    assert repo.buildings()[0]["tariff_tl_per_kwh"] == 4.2
