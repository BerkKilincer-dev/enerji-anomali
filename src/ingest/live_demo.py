"""Live demo: five virtual meters of one demo business, started and stopped from the panel.

Usage: python -m src.ingest.live_demo [--seconds-per-hour 60]
Runs as the compose service `live-demo`. On start it registers the business, its meters
(data/live_meters.csv) and its user, then waits. When an admin presses "Canlı demoyu başlat"
(POST /live-demo/start), it wipes the previous run and sends four weeks of backlog over MQTT at
once. The hours after that play fast, one hour every --seconds-per-hour seconds, until they
reach the current hour; from then on in real time. The readings are LEAD 2016, shifted by whole
weeks so they end now. Each meter carries one problem that starts close to where fast play
begins, so its alarm shows up within minutes of pressing start.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

import pandas as pd
import psycopg

from src.common.db import connect
from src.config import ROOT, require_database_url
from src.data.catalog import (
    EQUIPMENT_TYPES,
    building_ids,
    link_main_meter,
    upsert_asset,
    upsert_demo_user,
)
from src.data.customer import BuildingInfo, TenantInfo, register_building, register_tenant
from src.ingest.broker import start_client
from src.ingest.replay import HOUR, inject, local_now, shifted_series, stack

log = logging.getLogger(__name__)

LIVE_METERS_PATH = ROOT / "data" / "live_meters.csv"
TENANT = "Canlı Demo Tesisi"
PLAN = "uzman"  # operator: the business user can mark alarms
SEGMENT = "factory"
BACKLOG_DAYS = 28
# Fast play starts at 22:00, so the first night (00-05) comes two simulated hours in.
FAST_FROM_HOUR = 22
AHEAD = pd.Timedelta(days=7)  # hours kept ready for real-time play after catching up
# Where each problem starts, relative to the start of fast play: spike and stuck sit at the end
# of the backlog and show as soon as it is scored, the others a few simulated hours in.
INJECT_AT = {
    "spike": pd.Timedelta(hours=-10),
    "stuck": pd.Timedelta(hours=-24),
    "dropout": pd.Timedelta(hours=1),
    "night_base_load": pd.Timedelta(0),
}
# A daytime spike must stand four standard deviations above the past week to read as a spike
# rather than a model alarm; the scenario's default 3x is too small for that on these meters.
MAGNITUDE = {"spike": 5.0}
POLL_SECONDS = 1.0
PAUSE_SECONDS = 0.02  # between hours of a burst, so the broker never queues the whole backlog


def read_meters(path: Path = LIVE_METERS_PATH) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    problems = [
        f"unknown {column}: {value}"
        for column, allowed in (("equipment_type", EQUIPMENT_TYPES), ("scenario", INJECT_AT))
        for value in sorted(set(frame[column]) - set(allowed))
    ]
    if frame["external_id"].duplicated().any() or frame["location"].duplicated().any():
        problems.append("external_id and location must be unique")
    if problems:
        raise ValueError(f"invalid {path.name}: " + "; ".join(problems))
    return frame


def live_external_id(lead_id: int) -> str:
    return f"canli-{lead_id}"


def register(conn: psycopg.Connection, meters: pd.DataFrame, password: str | None) -> dict:
    """Business, meters, assets and user; returns LEAD building id -> live building id."""
    lead = building_ids(conn, meters["external_id"].tolist())
    meta = {
        row[0]: row[1:]
        for row in conn.execute(
            "SELECT building_id, primary_use, square_feet FROM buildings "
            "WHERE building_id = ANY(%s)",
            (list(lead.values()),),
        ).fetchall()
    }
    tenant_id = register_tenant(conn, TenantInfo(TENANT, plan=PLAN))
    live = {}
    for row in meters.to_dict("records"):
        lead_id = lead[row["external_id"]]
        primary_use, square_feet = meta[lead_id]
        external_id = live_external_id(lead_id)
        info = BuildingInfo(
            external_id,
            name=row["location"],
            primary_use=primary_use,
            square_feet=square_feet,
            tenant_id=tenant_id,
            segment=SEGMENT,
        )
        building_id = register_building(conn, info)
        link_main_meter(conn, building_id, external_id, upsert_asset(conn, building_id, row))
        live[lead_id] = building_id
    if password:
        upsert_demo_user(conn, tenant_id, TENANT, PLAN, password)
    conn.execute("UPDATE live_demo SET tenant_id = %s", (tenant_id,))
    return live


def reset(conn: psycopg.Connection, ids: list[int]) -> None:
    """Forget a previous run, so the next one scores and shows from a clean slate."""
    for table in ("anomaly_feedback", "anomalies", "predictions", "scoring_state", "readings"):
        conn.execute(f"DELETE FROM {table} WHERE building_id = ANY(%s)", (ids,))


def fast_start(now: pd.Timestamp) -> pd.Timestamp:
    """The latest FAST_FROM_HOUR at least a day before now: one to two days of fast play."""
    day_before = now - pd.Timedelta(days=1)
    start = day_before.normalize() + pd.Timedelta(hours=FAST_FROM_HOUR)
    return start if start <= day_before else start - pd.Timedelta(days=1)


def plan(
    readings: pd.DataFrame, meters: pd.DataFrame, lead: dict[str, int], now: pd.Timestamp
) -> tuple[pd.DataFrame, pd.Timestamp]:
    """Rows to send (ts, external_id, kwh) and the hour fast play starts after."""
    start = fast_start(now)
    # shifted_series moves whole weeks, so its first hour lands up to a week after now - days;
    # the extra week keeps at least BACKLOG_DAYS before fast play.
    days = BACKLOG_DAYS + 7 + -(-(now - start) // pd.Timedelta(days=1))
    series = {}
    for row in meters.to_dict("records"):
        lead_id = lead[row["external_id"]]
        kwh = shifted_series(readings, lead_id, days, now)
        kwh = kwh.loc[: now + AHEAD]
        scenario = row["scenario"]
        at = start + INJECT_AT[scenario]
        kwh = inject(kwh, scenario, lead_id, at, MAGNITUDE.get(scenario))
        series[live_external_id(lead_id)] = kwh
    return stack(series), start


def due_until(
    start: pd.Timestamp, elapsed_seconds: float, seconds_per_hour: float, now: pd.Timestamp
) -> pd.Timestamp:
    """Last hour to have been sent: the backlog at once, then one hour per seconds_per_hour,
    never an hour that has not ended yet."""
    fast = start + int(elapsed_seconds // seconds_per_hour) * HOUR
    return min(fast, now.floor("h") - HOUR)


def read_state(conn: psycopg.Connection) -> tuple[bool, int]:
    return conn.execute("SELECT running, run_id FROM live_demo").fetchone()


def beat(conn: psycopg.Connection, sim_ts: pd.Timestamp | None) -> None:
    conn.execute(
        "UPDATE live_demo SET heartbeat_at = now(), sim_ts = %s",
        (None if sim_ts is None else sim_ts.to_pydatetime(),),
    )


def send(client, rows: pd.DataFrame, pause: float = PAUSE_SECONDS) -> None:
    """One hour of all meters at a time, with a short pause between hours."""
    for ts, hour in rows.groupby("ts", sort=True):
        for external_id, kwh in zip(hour["external_id"], hour["kwh"], strict=True):
            kwh = None if pd.isna(kwh) else round(float(kwh), 4)
            client.publish(
                f"meters/{external_id}", json.dumps({"ts": ts.isoformat(), "kwh": kwh}), qos=1
            )
        time.sleep(pause)


def wait_for_lead(url: str, meters: pd.DataFrame, password: str | None) -> dict[int, int]:
    """Setup loads LEAD first; until then the meters cannot be registered."""
    while True:
        try:
            with connect(url) as conn:
                return register(conn, meters, password)
        except LookupError as exc:
            log.info("waiting for setup to load LEAD: %s", exc)
            time.sleep(10)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seconds-per-hour",
        type=float,
        default=float(os.getenv("LIVE_DEMO_SECONDS_PER_HOUR", "60")),
        help="real seconds per simulated hour during fast play",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    url = require_database_url()
    meters = read_meters()
    live = wait_for_lead(url, meters, os.getenv("DEMO_TENANT_PASSWORD") or None)
    with connect(url) as conn:
        lead = building_ids(conn, meters["external_id"].tolist())
    log.info("live demo ready: %d meters of %s; waiting for the start button", len(live), TENANT)

    client = start_client()
    run_id, rows, start, began, sent = None, None, None, 0.0, None
    while True:
        with connect(url) as conn:
            running, current = read_state(conn)
            if running and current != run_id:
                reset(conn, list(live.values()))
                readings = pd.DataFrame(
                    conn.execute(
                        "SELECT building_id, ts, kwh FROM readings WHERE building_id = ANY(%s) "
                        "ORDER BY ts",
                        (list(live),),
                    ).fetchall(),
                    columns=["building_id", "ts", "kwh"],
                )
                rows, start = plan(readings, meters, lead, local_now())
                run_id, began, sent = current, time.monotonic(), None
                log.info("run %d: backlog until %s, then fast play", run_id, start)
            if not running:
                rows = None
            if rows is not None:
                due = due_until(start, time.monotonic() - began, args.seconds_per_hour, local_now())
                mask = rows["ts"] <= due
                if sent is not None:
                    mask &= rows["ts"] > sent
                new = rows[mask]
                if not new.empty:
                    send(client, new)
                    sent = new["ts"].max()
            beat(conn, sent if running else None)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
