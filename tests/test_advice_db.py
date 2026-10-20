"""LLM advice against a real database (needs TEST_DATABASE_URL); the model is faked."""

from __future__ import annotations

import anthropic
import httpx

from src.common.db import connect
from src.scoring import advice
from tests.conftest import make_tables
from tests.test_db import count, db, empty_db, load_customer, pytestmark  # noqa: F401


def add_event(conn, building: int, hours_before_last: int, cost: float, origin="live") -> int:
    return conn.execute(
        "INSERT INTO anomalies (building_id, ts_start, ts_end, est_cost_tl, explanation, origin) "
        "SELECT %(b)s, t - interval '2 hours', t, %(cost)s, 'test', %(origin)s "
        "FROM (SELECT last_scored_ts - make_interval(hours => %(h)s) AS t FROM scoring_state "
        "WHERE building_id = %(b)s) s RETURNING id",
        {"b": building, "h": hours_before_last, "cost": cost, "origin": origin},
    ).fetchone()[0]


def test_advice_goes_to_settled_recent_live_events_once(db):  # noqa: F811
    building = load_customer(db, make_tables(building_ids=(100000,), days=10))[100000]
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO scoring_state (building_id, last_scored_ts) VALUES (%s, '2026-01-14')",
            (building,),
        )
        settled = add_event(conn, building, 10, cost=50)
        costly = add_event(conn, building, 20, cost=500)
        add_event(conn, building, 2, cost=900)  # may still be running
        add_event(conn, building, 24 * 30, cost=900)  # too old
        add_event(conn, building, 30, cost=900, origin="batch")
    asked = []

    def advise(event):
        asked.append(event["id"])
        return None if event["id"] == settled else f"öneri {event['id']}"

    with connect(db, autocommit=True) as conn:
        assert advice.write_advice(conn, advise) == 2
        assert advice.write_advice(conn, advise) == 0
    assert asked == [costly, settled]
    assert count(db, "SELECT count(*) FROM anomalies WHERE advice = %s", (f"öneri {costly}",)) == 1
    # A declined event is marked so it is not asked again.
    assert (
        count(db, "SELECT count(*) FROM anomalies WHERE id = %s AND advice = ''", (settled,)) == 1
    )


def test_advice_stops_at_the_first_api_error(db):  # noqa: F811
    building = load_customer(db, make_tables(building_ids=(100000,), days=10))[100000]
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO scoring_state (building_id, last_scored_ts) VALUES (%s, '2026-01-14')",
            (building,),
        )
        add_event(conn, building, 10, cost=50)
        add_event(conn, building, 20, cost=500)
    calls = []

    def overloaded(event):
        calls.append(event["id"])
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.APIConnectionError(request=request)

    with connect(db, autocommit=True) as conn:
        assert advice.write_advice(conn, overloaded) == 0
    assert len(calls) == 1
    assert count(db, "SELECT count(*) FROM anomalies WHERE advice IS NULL") == 2
