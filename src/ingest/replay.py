"""Virtual meters: replay LEAD buildings over MQTT as if they were live customers.

Usage: python -m src.ingest.replay (--building 1 [2 ...] | --split test) [--prefix demo]
           [--days 28] [--speed 0.2] [--register]
           [--inject night_base_load] [--inject-building 1] [--magnitude 2.5]
Timestamps move forward by whole weeks so the backlog ends now and weekdays still line up.
All buildings of one hour are sent together: the backlog quickly, later hours in real time.
Ctrl+C stops; a rerun resends the backlog, which is safe because readings are upserted.
"""

from __future__ import annotations

import argparse
import json
import logging
import time

import paho.mqtt.client as mqtt
import pandas as pd

from src.config import get_settings, require_database_url
from src.data import synthetic
from src.data.dataset import ensure_tables
from src.data.split import load_split
from src.ingest.broker import start_client

log = logging.getLogger(__name__)
HOUR = pd.Timedelta(hours=1)
WEEK = pd.Timedelta(weeks=1)
INJECT_AGE = pd.Timedelta(days=2)


def shifted_series(
    readings: pd.DataFrame, building_id: int, days: int, now: pd.Timestamp
) -> pd.Series:
    """kWh of one building indexed by replay time, starting `days` before now."""
    rows = readings[readings["building_id"] == building_id].sort_values("ts")
    if rows.empty:
        raise ValueError(f"building {building_id} has no readings")
    series = rows.set_index("ts")["kwh"].astype("float64")
    series = series.loc[series.first_valid_index() :]
    weeks = -(-(now - pd.Timedelta(days=days) - series.index[0]) // WEEK)
    series.index = series.index + weeks * WEEK
    return series


def inject_start(scenario: str, now: pd.Timestamp) -> pd.Timestamp:
    """Inside the backlog, but late enough that the scenario still runs at the current hour."""
    return now - min(INJECT_AGE, pd.Timedelta(hours=synthetic.SCENARIOS[scenario].hours - 1))


def inject(
    series: pd.Series,
    scenario: str,
    building_id: int,
    start: pd.Timestamp,
    magnitude: float | None = None,
) -> pd.Series:
    injection = synthetic.Injection(building_id, scenario, start.floor("h"), magnitude)
    kwh, _ = synthetic.apply(series, injection)
    return kwh


def stack(series: dict[str, pd.Series]) -> pd.DataFrame:
    """One row per (ts, external_id), sorted by replay time."""
    parts = [
        s.rename("kwh").rename_axis("ts").reset_index().assign(external_id=e)
        for e, s in series.items()
    ]
    return pd.concat(parts, ignore_index=True).sort_values(["ts", "external_id"], ignore_index=True)


def local_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=get_settings().default_timezone).tz_localize(None)


def publish(client: mqtt.Client, rows: pd.DataFrame, speed: float) -> None:
    for ts, hour in rows.groupby("ts", sort=True):
        wait = (ts + HOUR - local_now()).total_seconds()
        time.sleep(wait if wait > 0 else speed)
        for external_id, kwh in zip(hour["external_id"], hour["kwh"], strict=True):
            payload = {"ts": ts.isoformat(), "kwh": None if pd.isna(kwh) else round(float(kwh), 4)}
            client.publish(f"meters/{external_id}", json.dumps(payload), qos=1)


def register(ids: dict[int, str], buildings: pd.DataFrame) -> None:
    from src.common.db import connect
    from src.data.customer import BuildingInfo, register_building

    meta = buildings.set_index("building_id")
    with connect(require_database_url()) as conn:
        for building_id, external_id in ids.items():
            info = BuildingInfo(
                external_id,
                name=f"Demo building (LEAD {building_id})",
                primary_use=meta.at[building_id, "primary_use"],
                square_feet=float(meta.at[building_id, "square_feet"]),
            )
            log.info("registered %s as building %d", external_id, register_building(conn, info))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--building", type=int, nargs="+", help="LEAD building ids")
    which.add_argument(
        "--split", choices=["train", "val", "test"], help="every building of a split"
    )
    parser.add_argument("--prefix", default="demo", help="external ids are <prefix>-<building>")
    parser.add_argument("--days", type=int, default=28, help="backlog sent before real time")
    parser.add_argument("--speed", type=float, default=0.2, help="seconds per backlog hour")
    parser.add_argument("--register", action="store_true", help="register the buildings first")
    parser.add_argument("--inject", choices=sorted(synthetic.SCENARIOS))
    parser.add_argument("--inject-building", type=int, help="default: the first building")
    parser.add_argument("--magnitude", type=float, help="multiplier for spike, drift, night load")
    args = parser.parse_args()
    args.buildings = args.building or getattr(load_split(), args.split)
    if args.inject_building is not None and (
        not args.inject or args.inject_building not in args.buildings
    ):
        parser.error("--inject-building needs --inject and one of the replayed buildings")
    if args.magnitude is not None and (
        args.inject not in synthetic.SCALABLE or args.magnitude <= 0
    ):
        scalable = ", ".join(sorted(synthetic.SCALABLE))
        parser.error(f"--magnitude must be positive and needs --inject {scalable}")
    return args


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    tables = ensure_tables()
    ids = {b: f"{args.prefix}-{b}" for b in args.buildings}
    now = local_now().floor("h")
    series = {b: shifted_series(tables.readings, b, args.days, now) for b in ids}
    if args.inject:
        target = args.buildings[0] if args.inject_building is None else args.inject_building
        series[target] = inject(
            series[target], args.inject, target, inject_start(args.inject, now), args.magnitude
        )
        log.info("injected %s into %s", args.inject, ids[target])
    if args.register:
        register(ids, tables.buildings)

    rows = stack({ids[b]: s for b, s in series.items()})
    client = start_client()
    log.info("replaying %d buildings from %s", len(ids), rows["ts"].iloc[0])
    try:
        publish(client, rows, args.speed)
    except KeyboardInterrupt:
        log.info("stopped; rerun the same command to continue")
    finally:
        client.loop_stop()


if __name__ == "__main__":
    main()
