"""Create the shared schema, team roles and load the prepared tables into Postgres.

Usage: python -m src.data.load_db [--schema-only] [--force]
DATABASE_URL must point at the database owner. Optional roles come from
DB_TEAM_USER/DB_TEAM_PASSWORD (read-write) and DB_READER_USER/DB_READER_PASSWORD (read-only).
"""

from __future__ import annotations

import argparse
import logging
import os

import pandas as pd
import psycopg
from psycopg import sql

from src.common.db import SCHEMAS, connect
from src.config import ROOT, require_database_url
from src.data.dataset import ensure_tables
from src.data.prepare import Tables
from src.data.segments import BDG2_SEGMENTS
from src.data.split import assign_splits
from src.data.store import COLUMNS, copy_frame

log = logging.getLogger(__name__)

SCHEMA_PATH = ROOT / "db" / "init" / "001_schema.sql"
MIN_PASSWORD_LENGTH = 12
LEAD_DEPENDENTS = (
    "operating_hours",
    "planned_downtime",
    "work_orders",
    "alert_settings",
    "meters",
    "assets",
    "anomaly_feedback",
    "anomalies",
    "predictions",
    "scoring_state",
    "readings",
)


def apply_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA_PATH.read_text())
    # psql and DataGrip sessions also find the tables without a schema prefix.
    conn.execute(
        sql.SQL("ALTER DATABASE {} SET search_path = {}").format(
            sql.Identifier(conn.info.dbname),
            sql.SQL(", ").join(map(sql.Identifier, (*SCHEMAS, "public"))),
        )
    )
    # Buildings stored before segments existed get the default from their BDG2 primary use.
    conn.execute(
        "UPDATE buildings b SET segment = m.segment "
        "FROM unnest(%s::text[], %s::text[]) AS m (primary_use, segment) "
        "WHERE b.segment IS NULL AND b.primary_use = m.primary_use",
        (list(BDG2_SEGMENTS), list(BDG2_SEGMENTS.values())),
    )


def ensure_role(conn: psycopg.Connection, name: str, password: str, read_only: bool) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"password for role {name!r} must be at least {MIN_PASSWORD_LENGTH} characters"
        )
    conn.execute(
        sql.SQL("{} ROLE {} LOGIN PASSWORD {}").format(
            sql.SQL("ALTER" if role_exists(conn, name) else "CREATE"),
            sql.Identifier(name),
            sql.Literal(password),
        )
    )
    grant_rights(conn, name, read_only)


def role_exists(conn: psycopg.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone() is not None


def grant_rights(conn: psycopg.Connection, name: str, read_only: bool) -> None:
    table_rights = sql.SQL("SELECT" if read_only else "SELECT, INSERT, UPDATE, DELETE")
    statements = [
        "GRANT CONNECT ON DATABASE {db} TO {role}",
        "GRANT USAGE ON SCHEMA {schemas} TO {role}",
        "GRANT {rights} ON ALL TABLES IN SCHEMA {schemas} TO {role}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA {schemas} GRANT {rights} ON TABLES TO {role}",
    ]
    if not read_only:
        statements += [
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {schemas} TO {role}",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA {schemas} "
            "GRANT USAGE, SELECT ON SEQUENCES TO {role}",
        ]
    schemas = sql.SQL(", ").join(map(sql.Identifier, SCHEMAS))
    for statement in statements:
        conn.execute(
            sql.SQL(statement).format(
                db=sql.Identifier(conn.info.dbname),
                schemas=schemas,
                role=sql.Identifier(name),
                rights=table_rights,
            )
        )


def ensure_roles_from_env(conn: psycopg.Connection) -> list[str]:
    roles = []
    for prefix, read_only, default in (("DB_TEAM", False, "team"), ("DB_READER", True, "reader")):
        name = os.getenv(f"{prefix}_USER", default)
        password = os.getenv(f"{prefix}_PASSWORD")
        if password:
            ensure_role(conn, name, password, read_only)
        elif role_exists(conn, name):
            # Without a password the role keeps its login but still gets rights in new schemas.
            grant_rights(conn, name, read_only)
        else:
            continue
        roles.append(name)
    return roles


def db_frames(tables: Tables) -> dict[str, pd.DataFrame]:
    """Shape the processed tables like the database columns."""
    buildings = tables.buildings.assign(
        split=assign_splits(tables.buildings),
        segment=tables.buildings["primary_use"].map(BDG2_SEGMENTS),
    )
    weather = tables.weather.assign(source="bdg2")
    readings = tables.readings.merge(buildings[["building_id", "weather_key"]], on="building_id")
    readings = readings.merge(
        tables.weather[["weather_key", "ts", "air_temp"]], on=["weather_key", "ts"], how="left"
    )
    frames = {"buildings": buildings, "weather": weather, "readings": readings}
    return {name: frame[COLUMNS[name]] for name, frame in frames.items()}


def delete_lead(conn: psycopg.Connection) -> None:
    """Remove the LEAD dataset and its model output; customer data stays untouched."""
    lead = "SELECT building_id FROM buildings WHERE source = 'lead'"
    for table in LEAD_DEPENDENTS:
        conn.execute(
            sql.SQL("DELETE FROM {} WHERE building_id IN (" + lead + ")").format(
                sql.Identifier(table)
            )
        )
    conn.execute("DELETE FROM buildings WHERE source = 'lead'")
    conn.execute("DELETE FROM weather WHERE source = 'bdg2'")


def load_tables(conn: psycopg.Connection, tables: Tables, force: bool = False) -> bool:
    """Load the LEAD dataset in one transaction; returns False if already loaded."""
    query = "SELECT EXISTS (SELECT 1 FROM buildings WHERE source = 'lead')"
    loaded = conn.execute(query).fetchone()[0]
    if loaded and not force:
        return False
    if loaded:
        log.warning("--force: replacing the LEAD dataset and its model output")
        delete_lead(conn)
    for name, frame in db_frames(tables).items():
        copy_frame(conn, name, frame)
        log.info("loaded %s: %d rows", name, len(frame))
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema-only", action="store_true", help="create schema and roles only")
    parser.add_argument(
        "--force", action="store_true", help="reload data into a populated database"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    url = require_database_url()

    tables = None if args.schema_only else ensure_tables()
    with connect(url) as conn:
        apply_schema(conn)
        roles = ensure_roles_from_env(conn)
        if tables is not None and not load_tables(conn, tables, args.force):
            log.info("data already loaded, use --force to reload")
    log.info("schema ready%s", f", roles: {', '.join(roles)}" if roles else "")


if __name__ == "__main__":
    main()
