"""Data access for the API: Postgres when DATABASE_URL is set, a small demo set otherwise.

A repository is scoped to one tenant (`scoped(tenant_id)`) or to all of them (`None`, platform
staff). Every query filters by that scope, so a customer never sees another customer's
buildings; a building outside the scope answers as not found.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import NoReturn, Protocol

import numpy as np
import pandas as pd
import psycopg
from psycopg import errors
from psycopg.rows import dict_row

from src.api.auth import User
from src.common.db import connect
from src.data.customer import (
    BuildingInfo,
    TenantInfo,
    import_file,
    register_building,
    register_tenant,
)
from src.data.validate import ImportReport


class ReadOnlyError(RuntimeError):
    pass


class NotFoundError(LookupError):
    pass


class ConflictError(ValueError):
    pass


# Building rows visible to the current scope; queries alias buildings as b.
VISIBLE = "(%(tenant)s::int IS NULL OR b.tenant_id = %(tenant)s::int)"


class Repository(Protocol):
    mode: str

    def scoped(self, tenant_id: int | None) -> Repository: ...
    def buildings(self) -> list[dict]: ...
    def readings(self, building_id: int, hours: int, end: datetime | None = None) -> list[dict]: ...
    def forecast(self, building_id: int, hours: int, end: datetime | None = None) -> list[dict]: ...
    def anomalies(self, building_id: int | None, limit: int) -> list[dict]: ...
    def summary(self) -> dict: ...
    def tenants(self) -> list[dict]: ...
    def add_feedback(
        self, anomaly_id: int, verdict: str, note: str | None, created_by: str | None
    ) -> int: ...
    def register(self, info: BuildingInfo) -> int: ...
    def register_tenant(self, info: TenantInfo) -> int: ...
    def operating_hours(self, building_id: int) -> list[dict]: ...
    def set_operating_hours(self, building_id: int, hours: list[dict]) -> list[dict]: ...
    def downtime(self, building_id: int) -> list[dict]: ...
    def add_downtime(self, building_id: int, period: dict) -> int: ...
    def delete_downtime(self, building_id: int, downtime_id: int) -> None: ...
    def import_readings(
        self,
        building_id: int,
        raw: pd.DataFrame,
        unit: str,
        cumulative: bool,
        source_tz: str | None,
    ) -> ImportReport: ...
    # Accounts and sessions; these ignore the scope, the API checks rights first.
    def login_user(self, email: str) -> tuple[User, str | None] | None: ...
    def create_session(self, user_id: int, token_hash: str, seconds: int) -> None: ...
    def session_user(self, token_hash: str) -> User | None: ...
    def delete_session(self, token_hash: str) -> None: ...
    def create_user(
        self, tenant_id: int | None, email: str, name: str | None, role: str, password_hash: str
    ) -> int: ...
    def users(self) -> list[dict]: ...


def _user(row: dict) -> User:
    return User(row["user_id"], row["tenant_id"], row["email"], row["name"], row["role"])


class PostgresRepository:
    mode = "database"

    def __init__(self, database_url: str, tenant_id: int | None = None):
        self.url = database_url
        self.tenant_id = tenant_id

    def scoped(self, tenant_id: int | None) -> PostgresRepository:
        return PostgresRepository(self.url, tenant_id)

    def _params(self, **params: object) -> dict:
        return {"tenant": self.tenant_id, **params}

    @staticmethod
    def _rows(conn: psycopg.Connection, query: str, params: dict | tuple = ()) -> list[dict]:
        with conn.cursor(row_factory=dict_row) as cur:
            return cur.execute(query, params).fetchall()

    def _query(self, query: str, params: dict | tuple = ()) -> list[dict]:
        with connect(self.url) as conn:
            return self._rows(conn, query, params)

    def _require_building(
        self, conn: psycopg.Connection, building_id: int, lock: bool = False
    ) -> str:
        # The lock makes concurrent schedule writes wait; readings can still reference the row.
        query = f"SELECT external_id FROM buildings b WHERE building_id = %(id)s AND {VISIBLE}"
        row = conn.execute(
            query + " FOR NO KEY UPDATE" * lock, self._params(id=building_id)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"building {building_id} not found")
        return row[0]

    def _window(
        self, conn: psycopg.Connection, building_id: int, hours: int, end: datetime | None
    ) -> tuple:
        """`hours` hours ending at `end`, or at the building's latest reading."""
        self._require_building(conn, building_id)
        if end is None:
            end = conn.execute(
                "SELECT max(ts) FROM readings WHERE building_id = %s", (building_id,)
            ).fetchone()[0]
        if end is None:
            raise NotFoundError(f"building {building_id} has no readings")
        return end - timedelta(hours=hours - 1), end

    def buildings(self) -> list[dict]:
        return self._query(
            f"""
            SELECT b.building_id, b.external_id, b.name, b.source, b.primary_use, b.segment,
                   b.square_feet, b.timezone, b.country, b.lat, b.lng, b.tenant_id,
                   b.tariff_tl_per_kwh,
                   s.last_scored_ts,
                   (SELECT count(*) FROM current_anomalies a
                    WHERE a.building_id = b.building_id) AS anomaly_count
            FROM buildings b LEFT JOIN scoring_state s USING (building_id)
            WHERE b.active AND {VISIBLE} ORDER BY b.building_id
            """,
            self._params(),
        )

    def readings(self, building_id: int, hours: int, end: datetime | None = None) -> list[dict]:
        with connect(self.url) as conn:
            start, end = self._window(conn, building_id, hours, end)
            return self._rows(
                conn,
                "SELECT building_id, ts, kwh, anomaly FROM readings "
                "WHERE building_id = %s AND ts BETWEEN %s AND %s ORDER BY ts",
                (building_id, start, end),
            )

    def forecast(self, building_id: int, hours: int, end: datetime | None = None) -> list[dict]:
        with connect(self.url) as conn:
            start, end = self._window(conn, building_id, hours, end)
            return self._rows(
                conn,
                "SELECT building_id, ts, expected_kwh, alarm, source, origin "
                "FROM current_predictions WHERE building_id = %s AND ts BETWEEN %s AND %s "
                "ORDER BY ts",
                (building_id, start, end),
            )

    def anomalies(self, building_id: int | None, limit: int) -> list[dict]:
        return self._query(
            f"""
            SELECT a.id, a.building_id, a.ts_start, a.ts_end, a.score, a.source, a.rule_type,
                   a.excess_kwh, a.est_cost_tl, a.category, a.explanation, a.advice, a.origin,
                   (SELECT f.verdict FROM anomaly_feedback f
                    WHERE f.anomaly_id = a.id OR (f.anomaly_id IS NULL
                        AND f.building_id = a.building_id
                        AND f.ts_start <= a.ts_end AND f.ts_end >= a.ts_start)
                    ORDER BY f.created_at DESC LIMIT 1) AS verdict
            FROM current_anomalies a JOIN buildings b USING (building_id)
            WHERE (%(building)s::int IS NULL OR a.building_id = %(building)s::int) AND {VISIBLE}
            ORDER BY a.ts_start DESC LIMIT %(limit)s
            """,
            self._params(building=building_id, limit=limit),
        )

    def summary(self) -> dict:
        row = self._query(
            f"""
            WITH latest AS (
                SELECT b.building_id,
                       (SELECT max(ts) FROM readings r WHERE r.building_id = b.building_id)
                           AS end_ts
                FROM buildings b WHERE b.active AND {VISIBLE}
            ),
            costs AS (
                SELECT building_id, sum(est_cost_tl) AS cost FROM current_anomalies
                JOIN latest USING (building_id) GROUP BY 1
            )
            SELECT (SELECT COALESCE(sum(cost), 0) FROM costs) AS total_cost_tl,
                   (SELECT count(*) FROM current_anomalies a JOIN latest l USING (building_id)
                    WHERE a.ts_end >= l.end_ts - interval '24 hours') AS active_alerts,
                   (SELECT building_id FROM costs ORDER BY cost DESC NULLS LAST LIMIT 1)
                       AS worst_building_id,
                   (SELECT count(*) FROM latest) AS buildings
            """,
            self._params(),
        )
        return row[0]

    def tenants(self) -> list[dict]:
        return self._query(
            "SELECT tenant_id, name, status, city FROM tenants "
            "WHERE %(tenant)s::int IS NULL OR tenant_id = %(tenant)s::int ORDER BY name",
            self._params(),
        )

    def add_feedback(
        self, anomaly_id: int, verdict: str, note: str | None, created_by: str | None
    ) -> int:
        with connect(self.url) as conn:
            row = conn.execute(
                "INSERT INTO anomaly_feedback (anomaly_id, building_id, ts_start, ts_end, "
                "verdict, note, created_by) SELECT a.id, a.building_id, a.ts_start, a.ts_end, "
                "%(verdict)s, %(note)s, %(by)s FROM anomalies a JOIN buildings b "
                f"USING (building_id) WHERE a.id = %(id)s AND {VISIBLE} RETURNING id",
                self._params(verdict=verdict, note=note, by=created_by, id=anomaly_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"anomaly {anomaly_id} not found")
        return row[0]

    def register(self, info: BuildingInfo) -> int:
        if self.tenant_id is not None and info.tenant_id != self.tenant_id:
            raise NotFoundError(f"tenant {info.tenant_id} not found")
        try:
            with connect(self.url) as conn:
                # An external id taken by another tenant must not be updated from here.
                owner = conn.execute(
                    "SELECT tenant_id FROM buildings WHERE external_id = %s", (info.external_id,)
                ).fetchone()
                if owner is not None and self.tenant_id is not None and owner[0] != self.tenant_id:
                    raise ConflictError(f"external id {info.external_id!r} is taken")
                return register_building(conn, info)
        except errors.ForeignKeyViolation as exc:
            raise NotFoundError(f"tenant {info.tenant_id} not found") from exc

    def register_tenant(self, info: TenantInfo) -> int:
        try:
            with connect(self.url) as conn:
                return register_tenant(conn, info)
        except errors.UniqueViolation as exc:
            raise ConflictError("another tenant has this tax number") from exc

    def operating_hours(self, building_id: int) -> list[dict]:
        with connect(self.url) as conn:
            self._require_building(conn, building_id)
            return self._hours(conn, building_id)

    def set_operating_hours(self, building_id: int, hours: list[dict]) -> list[dict]:
        with connect(self.url) as conn:
            self._require_building(conn, building_id, lock=True)
            conn.execute("DELETE FROM operating_hours WHERE building_id = %s", (building_id,))
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO operating_hours (building_id, weekday, opens, closes) "
                    "VALUES (%s, %s, %s, %s)",
                    [(building_id, h["weekday"], h["opens"], h["closes"]) for h in hours],
                )
            return self._hours(conn, building_id)

    def _hours(self, conn: psycopg.Connection, building_id: int) -> list[dict]:
        return self._rows(
            conn,
            "SELECT weekday, opens, closes FROM operating_hours WHERE building_id = %s "
            "ORDER BY weekday, opens",
            (building_id,),
        )

    def downtime(self, building_id: int) -> list[dict]:
        with connect(self.url) as conn:
            self._require_building(conn, building_id)
            return self._rows(
                conn,
                "SELECT id, building_id, starts_at, ends_at, kind, note FROM planned_downtime "
                "WHERE building_id = %s ORDER BY starts_at",
                (building_id,),
            )

    def add_downtime(self, building_id: int, period: dict) -> int:
        with connect(self.url) as conn:
            self._require_building(conn, building_id)
            return conn.execute(
                "INSERT INTO planned_downtime (building_id, starts_at, ends_at, kind, note) "
                "VALUES (%(building_id)s, %(starts_at)s, %(ends_at)s, %(kind)s, %(note)s) "
                "RETURNING id",
                {**period, "building_id": building_id},
            ).fetchone()[0]

    def delete_downtime(self, building_id: int, downtime_id: int) -> None:
        with connect(self.url) as conn:
            self._require_building(conn, building_id)
            deleted = conn.execute(
                "DELETE FROM planned_downtime WHERE id = %s AND building_id = %s",
                (downtime_id, building_id),
            ).rowcount
        if not deleted:
            raise NotFoundError(f"downtime {downtime_id} not found for building {building_id}")

    def import_readings(
        self,
        building_id: int,
        raw: pd.DataFrame,
        unit: str,
        cumulative: bool,
        source_tz: str | None,
    ) -> ImportReport:
        with connect(self.url) as conn:
            external_id = self._require_building(conn, building_id)
            return import_file(
                conn, external_id, raw, unit=unit, cumulative=cumulative, source_tz=source_tz
            )

    def login_user(self, email: str) -> tuple[User, str | None] | None:
        rows = self._query(
            "SELECT user_id, tenant_id, email, name, role, password_hash FROM users "
            "WHERE email = %s AND active",
            (email,),
        )
        return (_user(rows[0]), rows[0]["password_hash"]) if rows else None

    def create_session(self, user_id: int, token_hash: str, seconds: int) -> None:
        with connect(self.url) as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < now()")
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) "
                "VALUES (%s, %s, now() + make_interval(secs => %s))",
                (token_hash, user_id, seconds),
            )

    def session_user(self, token_hash: str) -> User | None:
        rows = self._query(
            "SELECT u.user_id, u.tenant_id, u.email, u.name, u.role FROM sessions s "
            "JOIN users u USING (user_id) "
            "WHERE s.token_hash = %s AND s.expires_at > now() AND u.active",
            (token_hash,),
        )
        return _user(rows[0]) if rows else None

    def delete_session(self, token_hash: str) -> None:
        with connect(self.url) as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash,))

    def create_user(
        self, tenant_id: int | None, email: str, name: str | None, role: str, password_hash: str
    ) -> int:
        try:
            with connect(self.url) as conn:
                return conn.execute(
                    "INSERT INTO users (tenant_id, email, name, role, password_hash) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING user_id",
                    (tenant_id, email, name, role, password_hash),
                ).fetchone()[0]
        except errors.UniqueViolation as exc:
            raise ConflictError(f"a user with e-mail {email!r} exists") from exc
        except errors.ForeignKeyViolation as exc:
            raise NotFoundError(f"tenant {tenant_id} not found") from exc

    def users(self) -> list[dict]:
        return self._query(
            "SELECT user_id, tenant_id, email, name, role, active FROM users "
            "WHERE %(tenant)s::int IS NULL OR tenant_id = %(tenant)s::int ORDER BY email",
            self._params(),
        )


class DemoRepository:
    """Deterministic sample data so the frontend works without a database."""

    mode = "demo"

    END = datetime(2026, 10, 19, 23)
    WASTE = (datetime(2026, 10, 19, 1), datetime(2026, 10, 19, 5))
    BUILDINGS = [
        {
            "building_id": 100000,
            "external_id": "demo-office",
            "name": "Demo ofis",
            "source": "demo",
            "primary_use": "Office",
            "segment": "office",
            "square_feet": 54000.0,
            "timezone": "Europe/Istanbul",
            "country": "TR",
            "lat": 41.0082,
            "lng": 28.9784,
            "tenant_id": None,
            "tariff_tl_per_kwh": None,
            "last_scored_ts": END,
            "anomaly_count": 1,
        },
        {
            "building_id": 100001,
            "external_id": "demo-retail",
            "name": "Demo mağaza",
            "source": "demo",
            "primary_use": "Retail",
            "segment": "retail",
            "square_feet": 120000.0,
            "timezone": "Europe/Istanbul",
            "country": "TR",
            "lat": 40.99,
            "lng": 29.03,
            "tenant_id": None,
            "tariff_tl_per_kwh": None,
            "last_scored_ts": END,
            "anomaly_count": 0,
        },
    ]

    def scoped(self, tenant_id: int | None) -> DemoRepository:
        return self

    def _series(self, building_id: int, hours: int, end: datetime | None = None) -> pd.DataFrame:
        self._building(building_id)
        ts = pd.date_range(end=min(end or self.END, self.END), periods=hours, freq="h")
        rng = np.random.default_rng(building_id)
        day = ts.hour.isin(range(8, 19)) & (ts.dayofweek < 5)
        expected = np.where(day, 140.0, 90.0) * (1 + (building_id % 2) * 0.6)
        kwh = expected * rng.normal(1, 0.05, len(ts))
        night_waste = (ts >= self.WASTE[0]) & (ts <= self.WASTE[1])
        if building_id == 100000:
            kwh = np.where(night_waste, kwh * 1.6, kwh)
        return pd.DataFrame(
            {
                "building_id": building_id,
                "ts": ts,
                "kwh": kwh.round(2),
                "expected_kwh": expected.round(2),
                "anomaly": None,
                "alarm": night_waste & (building_id == 100000),
            }
        )

    def _building(self, building_id: int) -> dict:
        for b in self.BUILDINGS:
            if b["building_id"] == building_id:
                return b
        raise NotFoundError(f"building {building_id} not found")

    def buildings(self) -> list[dict]:
        return self.BUILDINGS

    def readings(self, building_id: int, hours: int, end: datetime | None = None) -> list[dict]:
        cols = ["building_id", "ts", "kwh", "anomaly"]
        return self._series(building_id, hours, end)[cols].to_dict("records")

    def forecast(self, building_id: int, hours: int, end: datetime | None = None) -> list[dict]:
        frame = self._series(building_id, hours, end).assign(origin="live")
        frame["source"] = pd.Series("rule", index=frame.index, dtype=object).where(frame["alarm"])
        cols = ["building_id", "ts", "expected_kwh", "alarm", "source", "origin"]
        return frame[cols].astype(object).where(frame[cols].notna(), None).to_dict("records")

    def anomalies(self, building_id: int | None, limit: int) -> list[dict]:
        item = {
            "id": 1,
            "building_id": 100000,
            "ts_start": self.WASTE[0],
            "ts_end": self.WASTE[1],
            "score": 0.91,
            "source": "rule",
            "rule_type": "night_base_load",
            "excess_kwh": 270.0,
            "est_cost_tl": 945.0,
            "explanation": "2026-10-19 01:00-06:00: Gece tüketimi son haftalardaki aynı saatin "
            "1,6 katına çıktı. Beklenenden yaklaşık 270 kWh fazla (3,50 TL/kWh ile 945 TL).",
            "advice": None,
            "category": "waste",
            "origin": "live",
            "verdict": None,
        }
        return [item] if building_id in (None, 100000) else []

    def summary(self) -> dict:
        return {
            "total_cost_tl": 945.0,
            "active_alerts": 1,
            "worst_building_id": 100000,
            "buildings": len(self.BUILDINGS),
        }

    def tenants(self) -> list[dict]:
        return []

    def operating_hours(self, building_id: int) -> list[dict]:
        self._building(building_id)
        return [{"weekday": d, "opens": time(8), "closes": time(19)} for d in range(1, 6)]

    def downtime(self, building_id: int) -> list[dict]:
        self._building(building_id)
        return []

    def users(self) -> list[dict]:
        return []

    def session_user(self, token_hash: str) -> User | None:
        return None

    def _read_only(self, *args: object, **kwargs: object) -> NoReturn:
        raise ReadOnlyError("demo mode has no database")

    add_feedback = register = register_tenant = _read_only
    set_operating_hours = add_downtime = delete_downtime = import_readings = _read_only
    login_user = create_session = delete_session = create_user = _read_only
