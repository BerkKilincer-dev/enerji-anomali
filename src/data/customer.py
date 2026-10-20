"""Onboard customer buildings and import their meter exports into the shared database.

Usage:
  python -m src.data.customer tenant --name "Acme A.S." [--tax-number 1234567890 ...]
  python -m src.data.customer register --external-id acme-hq [--tenant-id 1 --segment office ...]
  python -m src.data.customer import --external-id acme-hq --file export.csv \\
      [--ts-col timestamp --value-col kwh --unit kwh --cumulative --source-tz UTC]
DATABASE_URL must allow writes (the team role is enough).
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, timedelta
from pathlib import Path
from typing import BinaryIO, Literal, TypeVar, get_args

import pandas as pd
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from src.common.db import connect
from src.config import require_database_url
from src.data import weather_api
from src.data.prepare import WEATHER_COLUMNS, country_for_timezone
from src.data.segments import BDG2_SEGMENTS, SEGMENTS, Segment
from src.data.store import upsert_frame
from src.data.validate import ImportReport, normalize_readings

log = logging.getLogger(__name__)

WEATHER_CHUNK_DAYS = 365


@dataclass(frozen=True)
class BuildingInfo:
    external_id: str
    name: str | None = None
    lat: float | None = None
    lng: float | None = None
    timezone: str | None = None
    country: str | None = None
    primary_use: str | None = None
    square_feet: float | None = None
    year_built: int | None = None
    tariff_tl_per_kwh: float | None = None
    tenant_id: int | None = None
    segment: Segment | None = None

    def location(self) -> weather_api.Location:
        default = weather_api.Location.default()
        return weather_api.Location(
            self.lat if self.lat is not None else default.lat,
            self.lng if self.lng is not None else default.lng,
            self.timezone or default.timezone,
        )


TenantStatus = Literal["pilot", "active", "inactive"]


@dataclass(frozen=True)
class TenantInfo:
    name: str
    tax_number: str | None = None
    tax_office: str | None = None
    city: str | None = None
    address: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    plan: str | None = None
    status: TenantStatus | None = None
    dpa_signed_on: date | None = None


def register_tenant(conn: psycopg.Connection, info: TenantInfo) -> int:
    """Create a tenant or update the given fields of the one with this name; returns its id."""
    values = {k: v for k, v in asdict(info).items() if v is not None}
    columns = list(values)
    row = conn.execute(
        sql.SQL(
            "INSERT INTO tenants ({}) VALUES ({}) ON CONFLICT (name) DO UPDATE SET {} "
            "RETURNING tenant_id"
        ).format(
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.SQL(", ").join(sql.Placeholder() * len(columns)),
            sql.SQL(", ").join(
                sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in columns
            ),
        ),
        list(values.values()),
    ).fetchone()
    return row[0]


def register_building(conn: psycopg.Connection, info: BuildingInfo) -> int:
    """Create or update a customer building; returns its building_id."""
    stored = conn.execute(
        "SELECT lat, lng, timezone, country, segment FROM buildings WHERE external_id = %s",
        (info.external_id,),
    ).fetchone()
    if stored:
        # An update keeps the stored location and segment, not the Istanbul or BDG2 default.
        lat, lng, timezone, country, segment = stored
        info = replace(
            info,
            lat=lat if info.lat is None else info.lat,
            lng=lng if info.lng is None else info.lng,
            timezone=info.timezone or timezone,
            country=info.country or country,
            segment=info.segment or segment,
        )
    loc = info.location()
    country = (info.country or country_for_timezone(loc.timezone) or "").upper() or None
    row = conn.execute(
        """
        INSERT INTO buildings (external_id, name, source, weather_key, primary_use, square_feet,
                               year_built, timezone, country, lat, lng, tariff_tl_per_kwh,
                               tenant_id, segment)
        VALUES (%s, %s, 'customer', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (external_id) DO UPDATE SET
            name = COALESCE(EXCLUDED.name, buildings.name),
            tenant_id = COALESCE(EXCLUDED.tenant_id, buildings.tenant_id),
            segment = COALESCE(EXCLUDED.segment, buildings.segment),
            weather_key = EXCLUDED.weather_key,
            primary_use = COALESCE(EXCLUDED.primary_use, buildings.primary_use),
            square_feet = COALESCE(EXCLUDED.square_feet, buildings.square_feet),
            year_built = COALESCE(EXCLUDED.year_built, buildings.year_built),
            timezone = EXCLUDED.timezone, country = EXCLUDED.country,
            lat = EXCLUDED.lat, lng = EXCLUDED.lng,
            tariff_tl_per_kwh = COALESCE(EXCLUDED.tariff_tl_per_kwh, buildings.tariff_tl_per_kwh)
        RETURNING building_id
        """,
        (
            info.external_id,
            info.name,
            loc.weather_key,
            info.primary_use,
            info.square_feet,
            info.year_built,
            loc.timezone,
            country,
            loc.lat,
            loc.lng,
            info.tariff_tl_per_kwh,
            info.tenant_id,
            info.segment or BDG2_SEGMENTS.get(info.primary_use),
        ),
    ).fetchone()
    return row[0]


def building_row(conn: psycopg.Connection, external_id: str) -> dict:
    row = (
        conn.cursor(row_factory=dict_row)
        .execute(
            "SELECT building_id, timezone FROM buildings WHERE external_id = %s", (external_id,)
        )
        .fetchone()
    )
    if row is None:
        raise LookupError(f"building {external_id!r} is not registered")
    return row


def upsert_readings(
    conn: psycopg.Connection, building_id: int, hourly: pd.DataFrame, source: str
) -> int:
    """Insert or replace hourly readings; existing anomaly labels are kept."""
    rows = hourly[["ts", "kwh", "meter_raw", "quality"]].assign(
        building_id=building_id, source=source
    )
    return upsert_frame(conn, "readings", rows, ("building_id", "ts"), stamp="received_at")


def upsert_weather(conn: psycopg.Connection, weather: pd.DataFrame, source: str) -> int:
    rows = weather[["weather_key", "ts", *WEATHER_COLUMNS]].assign(source=source)
    return upsert_frame(conn, "weather", rows, ("weather_key", "ts"))


def missing_weather_days(
    conn: psycopg.Connection, building_id: int, weather_key: str, since: date | None = None
) -> list[date]:
    rows = conn.execute(
        """
        SELECT DISTINCT r.ts::date FROM readings r
        WHERE r.building_id = %s AND r.ts >= %s AND NOT EXISTS (
            SELECT 1 FROM weather w WHERE w.weather_key = %s AND w.ts = r.ts)
        ORDER BY 1
        """,
        (building_id, since or date.min, weather_key),
    ).fetchall()
    return [r[0] for r in rows]


def backfill_weather(
    conn: psycopg.Connection,
    building_id: int,
    fetch: Callable | None = None,
    since: date | None = None,
) -> int:
    """Fetch Open-Meteo weather for reading hours that have none; site weather is left alone."""
    fetch = fetch or weather_api.fetch
    b = conn.execute(
        "SELECT weather_key, lat, lng, timezone FROM buildings WHERE building_id = %s",
        (building_id,),
    ).fetchone()
    if b is None or not b[0].startswith("geo:") or b[1] is None:
        return 0
    loc = weather_api.Location(b[1], b[2], b[3])
    days = missing_weather_days(conn, building_id, loc.weather_key, since)
    written = 0
    while days:
        start = days[0]
        end = min(days[-1], start + timedelta(days=WEATHER_CHUNK_DAYS - 1))
        written += upsert_weather(conn, fetch(loc, start, end), "open-meteo")
        days = [d for d in days if d > end]
    conn.execute(
        "UPDATE readings r SET air_temp = w.air_temp FROM weather w "
        "WHERE r.building_id = %s AND r.ts >= %s AND w.weather_key = %s AND w.ts = r.ts "
        "AND r.air_temp IS DISTINCT FROM w.air_temp",
        (building_id, since or date.min, loc.weather_key),
    )
    return written


def try_backfill_weather(
    conn: psycopg.Connection,
    building_id: int,
    fetch: Callable | None = None,
    since: date | None = None,
) -> int | None:
    """`backfill_weather` in its own transaction; a failure is logged and returns None."""
    try:
        with conn.transaction():
            return backfill_weather(conn, building_id, fetch, since)
    except (OSError, ValueError, psycopg.Error) as exc:
        log.warning("building %d: weather backfill failed, retry later: %s", building_id, exc)
        return None


def read_export(
    source: Path | BinaryIO, ts_col: str, value_col: str, sep: str | None, decimal: str
) -> pd.DataFrame:
    frame = pd.read_csv(
        source,
        sep=sep,
        decimal=decimal,
        engine="python" if sep is None else "c",
        usecols=[ts_col, value_col],
        dtype={ts_col: str},
    )
    return frame.rename(columns={ts_col: "ts", value_col: "value"})


def import_file(
    conn: psycopg.Connection,
    external_id: str,
    raw: pd.DataFrame,
    *,
    unit: str = "kwh",
    cumulative: bool = False,
    source_tz: str | None = None,
    source: str = "csv",
    weather: bool = True,
) -> ImportReport:
    building = building_row(conn, external_id)
    hourly, report = normalize_readings(raw, building["timezone"], unit, cumulative, source_tz)
    written = upsert_readings(conn, building["building_id"], hourly, source)
    log.info("%s: %d hourly readings written", external_id, written)
    # Readings are kept even when the weather call below is slow or fails.
    conn.commit()
    if weather and (fetched := try_backfill_weather(conn, building["building_id"])) is not None:
        log.info("%s: %d weather hours fetched", external_id, fetched)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    ten = sub.add_parser("tenant", help="create or update a tenant")
    ten.add_argument("--name", required=True)
    details = "tax-number tax-office city address contact-name contact-email contact-phone plan"
    for name in details.split():
        ten.add_argument(f"--{name}")
    ten.add_argument("--status", choices=get_args(TenantStatus))
    ten.add_argument("--dpa-signed-on", type=date.fromisoformat, help="YYYY-MM-DD")
    reg = sub.add_parser("register", help="create or update a building")
    reg.add_argument("--external-id", required=True)
    for name in ("name", "timezone", "country", "primary-use"):
        reg.add_argument(f"--{name}")
    for name in ("lat", "lng", "square-feet"):
        reg.add_argument(f"--{name}", type=float)
    reg.add_argument("--tariff", type=float, dest="tariff_tl_per_kwh")
    reg.add_argument("--year-built", type=int)
    reg.add_argument("--tenant-id", type=int)
    reg.add_argument("--segment", choices=SEGMENTS)
    imp = sub.add_parser("import", help="import a meter export (CSV)")
    imp.add_argument("--external-id", required=True)
    imp.add_argument("--file", type=Path, required=True)
    imp.add_argument("--ts-col", default="timestamp")
    imp.add_argument("--value-col", default="kwh")
    imp.add_argument("--unit", default="kwh", help="wh, kwh, mwh or kbtu")
    imp.add_argument("--cumulative", action="store_true", help="values are a register reading")
    imp.add_argument("--source-tz", help="timezone of naive timestamps, default building timezone")
    imp.add_argument("--sep", help="column separator, sniffed when omitted")
    imp.add_argument("--decimal", default=".")
    imp.add_argument("--no-weather", action="store_true")
    return parser


Info = TypeVar("Info", TenantInfo, BuildingInfo)


def _from_args(cls: type[Info], args: argparse.Namespace) -> Info:
    return cls(**{f.name: getattr(args, f.name) for f in fields(cls)})


def main() -> None:
    args = _parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    url = require_database_url()
    with connect(url) as conn:
        if args.command == "tenant":
            print(f"tenant_id {register_tenant(conn, _from_args(TenantInfo, args))}")
            return
        if args.command == "register":
            print(f"building_id {register_building(conn, _from_args(BuildingInfo, args))}")
            return
        raw = read_export(args.file, args.ts_col, args.value_col, args.sep, args.decimal)
        report = import_file(
            conn,
            args.external_id,
            raw,
            unit=args.unit,
            cumulative=args.cumulative,
            source_tz=args.source_tz,
            weather=not args.no_weather,
        )
    print(json.dumps(report.as_dict(), indent=2))


if __name__ == "__main__":
    main()
