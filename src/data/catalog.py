"""Assign LEAD buildings to demo tenants from the meter catalog and create their demo users.

Usage: python -m src.data.catalog [--file data/meter_catalog.csv]
DATABASE_URL must allow writes. DEMO_TENANT_PASSWORD (12+ characters) is the password of the
demo users, set again on every run so a changed .env applies; without it tenants, buildings,
assets and meters are loaded and users are skipped.
The run is one transaction and can be repeated: rows are matched and updated, never duplicated.
"""

from __future__ import annotations

import argparse
import logging
import os
import unicodedata
from pathlib import Path

import pandas as pd
import psycopg

from src.common.db import connect
from src.config import ROOT, require_database_url
from src.data.customer import TenantInfo, register_tenant
from src.data.segments import SEGMENTS

log = logging.getLogger(__name__)

CATALOG_PATH = ROOT / "data" / "meter_catalog.csv"
# Same list as the CHECK on core.assets.equipment_type.
EQUIPMENT_TYPES = ("klima", "kompresor", "aydinlatma", "sogutma", "plc", "genel")
PLAN_ROLES = {
    "izleme": "viewer",
    "operasyon": "operator",
    "analiz": "operator",
    "uzman": "operator",
    "kurumsal": "operator",
}
REQUIRED_COLUMNS = (
    "external_id",
    "tenant",
    "plan",
    "segment",
    "location",
    "asset_name",
    "equipment_type",
)
DEMO_EMAIL_DOMAIN = "enerji.local"


def read_catalog(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if "tariff_tl_per_kwh" in frame:
        frame["tariff_tl_per_kwh"] = pd.to_numeric(frame["tariff_tl_per_kwh"], errors="coerce")
    return frame


def catalog_problems(frame: pd.DataFrame) -> list[str]:
    """Everything wrong with the catalog; an empty list means it can be loaded."""
    missing = [c for c in REQUIRED_COLUMNS if c not in frame]
    if missing:
        return [f"missing columns: {', '.join(missing)}"]
    problems = []
    for column in REQUIRED_COLUMNS:
        if (frame[column].str.strip() == "").any():
            problems.append(f"empty {column} values")
    duplicated = frame.loc[frame["external_id"].duplicated(), "external_id"]
    if not duplicated.empty:
        problems.append(f"duplicate external_id: {', '.join(sorted(set(duplicated)))}")
    for column, allowed in (
        ("equipment_type", EQUIPMENT_TYPES),
        ("segment", SEGMENTS),
        ("plan", tuple(PLAN_ROLES)),
    ):
        unknown = sorted(set(frame[column]) - set(allowed))
        if unknown:
            problems.append(f"unknown {column}: {', '.join(unknown)}")
    plans = frame.groupby("tenant")["plan"].nunique()
    for tenant in plans[plans > 1].index:
        problems.append(f"tenant {tenant!r} has more than one plan")
    # The location becomes the building name, so a tenant must not use one twice.
    repeated = frame.loc[frame.duplicated(["tenant", "location"]), ["tenant", "location"]]
    for tenant, location in repeated.drop_duplicates().itertuples(index=False):
        problems.append(f"tenant {tenant!r} repeats location {location!r}")
    if "tariff_tl_per_kwh" in frame and (frame["tariff_tl_per_kwh"] < 0).any():
        problems.append("negative tariff_tl_per_kwh")
    return problems


def demo_email(tenant: str) -> str:
    """'Üniversite Kampüsü' -> 'universitekampusu@enerji.local'."""
    ascii_name = unicodedata.normalize("NFKD", tenant.replace("ı", "i").replace("İ", "I"))
    local = "".join(c for c in ascii_name if c.isascii() and c.isalnum()).lower()
    return f"{local}@{DEMO_EMAIL_DOMAIN}"


def building_ids(conn: psycopg.Connection, external_ids: list[str]) -> dict[str, int]:
    rows = conn.execute(
        "SELECT external_id, building_id FROM buildings WHERE external_id = ANY(%s)",
        (external_ids,),
    ).fetchall()
    found = dict(rows)
    unknown = sorted(set(external_ids) - set(found))
    if unknown:
        raise LookupError(f"buildings not in the database: {', '.join(unknown)}")
    return found


def assign_building(conn: psycopg.Connection, building_id: int, tenant_id: int, row: dict) -> None:
    """Set tenant, name, segment and tariff only; location and weather stay as loaded."""
    tariff = row.get("tariff_tl_per_kwh")
    conn.execute(
        "UPDATE buildings SET tenant_id = %s, name = %s, segment = %s, "
        "tariff_tl_per_kwh = COALESCE(%s, tariff_tl_per_kwh) WHERE building_id = %s",
        (
            tenant_id,
            row["location"],
            row["segment"],
            None if pd.isna(tariff) else tariff,
            building_id,
        ),
    )


def upsert_asset(conn: psycopg.Connection, building_id: int, row: dict) -> int:
    """One asset per building and name; a whole-building meter is a zone, the rest machines."""
    kind = "zone" if row["equipment_type"] == "genel" else "machine"
    found = conn.execute(
        "UPDATE assets SET kind = %s, equipment_type = %s "
        "WHERE building_id = %s AND name = %s RETURNING asset_id",
        (kind, row["equipment_type"], building_id, row["asset_name"]),
    ).fetchone()
    if found:
        return found[0]
    return conn.execute(
        "INSERT INTO assets (building_id, kind, name, equipment_type) "
        "VALUES (%s, %s, %s, %s) RETURNING asset_id",
        (building_id, kind, row["asset_name"], row["equipment_type"]),
    ).fetchone()[0]


def link_main_meter(
    conn: psycopg.Connection, building_id: int, external_id: str, asset_id: int
) -> None:
    """The building's readings come from its main meter, which now measures the asset."""
    updated = conn.execute(
        "UPDATE meters SET asset_id = %s WHERE building_id = %s AND is_main",
        (asset_id, building_id),
    ).rowcount
    if not updated:
        conn.execute(
            "INSERT INTO meters (building_id, asset_id, external_id, is_main) "
            "VALUES (%s, %s, %s, true)",
            (building_id, asset_id, external_id),
        )


def upsert_demo_user(
    conn: psycopg.Connection, tenant_id: int, tenant: str, plan: str, password: str
) -> str:
    """Role follows the plan and the password follows `password` on every run."""
    from src.api.auth import hash_password, verify_password

    email = demo_email(tenant)
    row = conn.execute("SELECT password_hash FROM users WHERE email = %s", (email,)).fetchone()
    # Hashing is slow on purpose; keep the stored hash when the password has not changed.
    same = row is not None and verify_password(password, row[0])
    conn.execute(
        "INSERT INTO users (tenant_id, email, name, role, password_hash) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (email) DO UPDATE SET tenant_id = EXCLUDED.tenant_id, role = EXCLUDED.role, "
        "password_hash = EXCLUDED.password_hash",
        (
            tenant_id,
            email,
            f"{tenant} yöneticisi",
            PLAN_ROLES[plan],
            row[0] if same else hash_password(password),
        ),
    )
    return email


def load_catalog(
    conn: psycopg.Connection, frame: pd.DataFrame, password: str | None = None
) -> dict[str, int]:
    """Write the catalog into the database; returns how many rows of each kind were written."""
    problems = catalog_problems(frame)
    if problems:
        raise ValueError("invalid catalog: " + "; ".join(problems))
    ids = building_ids(conn, frame["external_id"].tolist())
    plans = frame.groupby("tenant")["plan"].first()
    tenants = {
        name: register_tenant(conn, TenantInfo(name, plan=plan)) for name, plan in plans.items()
    }
    for row in frame.to_dict("records"):
        building_id = ids[row["external_id"]]
        assign_building(conn, building_id, tenants[row["tenant"]], row)
        asset_id = upsert_asset(conn, building_id, row)
        link_main_meter(conn, building_id, row["external_id"], asset_id)
    users = 0
    if password:
        for name, plan in plans.items():
            upsert_demo_user(conn, tenants[name], name, plan, password)
            users += 1
    return {"tenants": len(tenants), "buildings": len(frame), "users": users}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=CATALOG_PATH)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    url = require_database_url()
    password = os.getenv("DEMO_TENANT_PASSWORD") or None
    if password is None:
        log.warning("DEMO_TENANT_PASSWORD is not set, demo users are skipped")
    frame = read_catalog(args.file)
    with connect(url) as conn:
        written = load_catalog(conn, frame, password)
    log.info(
        "catalog loaded: %(tenants)d tenants, %(buildings)d buildings, %(users)d users", written
    )


if __name__ == "__main__":
    main()
