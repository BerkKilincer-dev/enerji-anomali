"""Bulk reads and writes between pandas frames and the shared Postgres tables."""

from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
import psycopg
from psycopg import sql

from src.data.prepare import WEATHER_COLUMNS, Tables

CHUNK_ROWS = 200_000
DATA_TABLES = ("buildings", "weather", "readings")
COLUMNS = {
    "buildings": [
        "building_id",
        "external_id",
        "source",
        "site_id",
        "weather_key",
        "primary_use",
        "sub_use",
        "square_feet",
        "year_built",
        "timezone",
        "country",
        "lat",
        "lng",
        "split",
        "segment",
    ],
    "weather": ["weather_key", "ts", *WEATHER_COLUMNS, "source"],
    "readings": ["building_id", "ts", "kwh", "air_temp", "meter_raw", "anomaly", "source"],
}
READ_COLUMNS = {
    "buildings": [*COLUMNS["buildings"], "name", "tariff_tl_per_kwh"],
    "weather": ["weather_key", "ts", *WEATHER_COLUMNS],
    "readings": ["building_id", "ts", "kwh", "meter_raw", "quality", "anomaly", "source"],
}
DTYPES = {
    "buildings": {
        "building_id": "int32",
        "site_id": "Int16",
        "square_feet": "float64",
        "year_built": "Int16",
        "lat": "float64",
        "lng": "float64",
        "tariff_tl_per_kwh": "float64",
    },
    "weather": dict.fromkeys(WEATHER_COLUMNS, "float32"),
    "readings": {
        "building_id": "int32",
        "kwh": "float32",
        "meter_raw": "float32",
        "anomaly": "Int8",
    },
}
ORDER = {"buildings": "building_id", "weather": "weather_key, ts", "readings": "building_id, ts"}


def copy_frame(conn: psycopg.Connection, table: str, frame: pd.DataFrame) -> None:
    query = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv)").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, frame.columns))
    )
    with conn.cursor().copy(query) as copy:
        for start in range(0, len(frame), CHUNK_ROWS):
            copy.write(frame.iloc[start : start + CHUNK_ROWS].to_csv(index=False, header=False))


def upsert_frame(
    conn: psycopg.Connection,
    table: str,
    frame: pd.DataFrame,
    keys: tuple[str, ...],
    stamp: str | None = None,
) -> int:
    """Insert rows and overwrite the other columns of rows whose keys already exist."""
    if frame.empty:
        return 0
    target, stage = sql.Identifier(table), sql.Identifier(f"stage_{table}")
    conn.execute(
        sql.SQL(
            "CREATE TEMP TABLE IF NOT EXISTS {} ON COMMIT DELETE ROWS "
            "AS SELECT * FROM {} WITH NO DATA"
        ).format(stage, target)
    )
    copy_frame(conn, f"stage_{table}", frame)
    columns = sql.SQL(", ").join(map(sql.Identifier, frame.columns))
    updates = [
        sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c))
        for c in frame.columns
        if c not in keys
    ]
    if stamp:
        updates.append(sql.SQL("{} = now()").format(sql.Identifier(stamp)))
    cur = conn.execute(
        sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {} ON CONFLICT ({}) DO UPDATE SET {}").format(
            target,
            columns,
            columns,
            stage,
            sql.SQL(", ").join(map(sql.Identifier, keys)),
            sql.SQL(", ").join(updates),
        )
    )
    conn.execute(sql.SQL("TRUNCATE {}").format(stage))
    return cur.rowcount


def _filters(name: str, building_ids: list[int] | None, since: datetime | None) -> sql.Composable:
    clauses = []
    if building_ids is not None:
        ids = sql.Literal(list(map(int, building_ids)))
        clauses.append(
            sql.SQL(
                "weather_key IN (SELECT weather_key FROM buildings WHERE building_id = ANY({}))"
            ).format(ids)
            if name == "weather"
            else sql.SQL("building_id = ANY({})").format(ids)
        )
    if since is not None and name != "buildings":
        clauses.append(sql.SQL("ts >= {}").format(sql.Literal(since)))
    return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses) if clauses else sql.SQL("")


def fetch_tables(
    conn: psycopg.Connection, building_ids: list[int] | None = None, since: datetime | None = None
) -> Tables:
    """Read tables back with the parquet dtypes, optionally for some buildings and recent hours."""
    frames = {}
    with conn.cursor() as cur:
        for name in DATA_TABLES:
            query = sql.SQL(
                "COPY (SELECT {} FROM {}{} ORDER BY {}) TO STDOUT WITH (FORMAT csv, HEADER)"
            )
            query = query.format(
                sql.SQL(", ").join(map(sql.Identifier, READ_COLUMNS[name])),
                sql.Identifier(name),
                _filters(name, building_ids, since),
                sql.SQL(ORDER[name]),
            )
            buffer = io.BytesIO()
            with cur.copy(query) as copy:
                for chunk in copy:
                    buffer.write(chunk)
            buffer.seek(0)
            frame = pd.read_csv(
                buffer,
                true_values=["t"],
                false_values=["f"],
                parse_dates=["ts"] if "ts" in READ_COLUMNS[name] else None,
            )
            frames[name] = frame.astype(DTYPES[name])
    return Tables(**frames)
