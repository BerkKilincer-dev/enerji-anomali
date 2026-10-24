"""Request and response bodies. Timestamps are building local time without an offset."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal
from zoneinfo import available_timezones

from pydantic import (
    BaseModel,
    Field,
    NaiveDatetime,
    RootModel,
    field_validator,
    model_validator,
)

from src.api.auth import MIN_PASSWORD_LENGTH, Role, User
from src.data.customer import TenantStatus
from src.data.segments import Segment

INT_MAX = 2**31 - 1
BIGINT_MAX = 2**63 - 1
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class Building(BaseModel):
    building_id: int
    external_id: str
    name: str | None
    source: str
    primary_use: str | None
    segment: str | None
    square_feet: float | None
    timezone: str
    country: str | None
    lat: float | None
    lng: float | None
    tenant_id: int | None
    tariff_tl_per_kwh: float
    last_scored_ts: datetime | None
    anomaly_count: int


class Reading(BaseModel):
    building_id: int
    ts: datetime
    kwh: float | None
    anomaly: int | None


class ForecastPoint(BaseModel):
    building_id: int
    ts: datetime
    expected_kwh: float | None
    alarm: bool
    source: str | None
    origin: str


class Anomaly(BaseModel):
    id: int
    building_id: int
    ts_start: datetime
    ts_end: datetime
    score: float | None
    source: str | None
    rule_type: str | None
    excess_kwh: float | None
    est_cost_tl: float | None
    explanation: str | None
    advice: str | None
    category: str | None
    origin: str
    verdict: str | None


class Summary(BaseModel):
    total_cost_tl: float
    active_alerts: int
    worst_building_id: int | None
    buildings: int


class BuildingIn(BaseModel):
    external_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    name: str | None = Field(None, max_length=200)
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    timezone: str | None = None
    country: str | None = Field(None, pattern=r"^[A-Za-z]{2}$")
    primary_use: str | None = Field(None, max_length=100)
    square_feet: float | None = Field(None, gt=0)
    year_built: int | None = Field(None, ge=1800, le=2100)
    tariff_tl_per_kwh: float | None = Field(None, ge=0)
    tenant_id: int | None = Field(None, gt=0, le=INT_MAX)
    segment: Segment | None = None

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str | None) -> str | None:
        if value is not None and value not in available_timezones():
            raise ValueError(f"unknown timezone {value!r}")
        return value


class TenantIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    tax_number: str | None = Field(None, pattern=r"^[0-9]{10,11}$")
    tax_office: str | None = Field(None, max_length=100)
    city: str | None = Field(None, max_length=100)
    address: str | None = Field(None, max_length=500)
    contact_name: str | None = Field(None, max_length=200)
    contact_email: str | None = Field(None, max_length=254, pattern=EMAIL)
    contact_phone: str | None = Field(None, pattern=r"^\+?[0-9 ()-]{7,20}$")
    plan: str | None = Field(None, max_length=50)
    status: TenantStatus | None = None
    dpa_signed_on: date | None = None


class OperatingHours(BaseModel):
    weekday: int = Field(ge=1, le=7)
    opens: time
    closes: time


DAY_SECONDS = 86_400
WEEK_SECONDS = 7 * DAY_SECONDS


def _seconds(value: time) -> int:
    return value.hour * 3600 + value.minute * 60 + value.second


class Schedule(RootModel[list[OperatingHours]]):
    root: list[OperatingHours] = Field(max_length=70)

    @model_validator(mode="after")
    def no_overlap(self) -> Schedule:
        # Periods on a weekly circle in seconds; each must end before the next one starts.
        periods = sorted(
            (
                (p.weekday - 1) * DAY_SECONDS + _seconds(p.opens),
                (_seconds(p.closes) - _seconds(p.opens)) % DAY_SECONDS or DAY_SECONDS,
            )
            for p in self.root
        )
        ends = [start + length for start, length in periods]
        nexts = [start for start, _ in periods[1:]] + [s + WEEK_SECONDS for s, _ in periods[:1]]
        if any(end > next_start for end, next_start in zip(ends, nexts, strict=True)):
            raise ValueError("periods overlap")
        return self


DowntimeKind = Literal["holiday", "maintenance", "shutdown"]


class DowntimeIn(BaseModel):
    starts_at: NaiveDatetime
    ends_at: NaiveDatetime
    kind: DowntimeKind
    note: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def ends_after_start(self) -> DowntimeIn:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class Downtime(DowntimeIn):
    id: int
    building_id: int


class Created(BaseModel):
    id: int


class FeedbackIn(BaseModel):
    verdict: Literal["confirmed", "false_alarm", "planned"]
    note: str | None = Field(None, max_length=1000)


class LoginIn(BaseModel):
    email: str = Field(max_length=254, pattern=EMAIL)
    password: str = Field(min_length=1, max_length=200)


class Me(BaseModel):
    user_id: int
    tenant_id: int | None
    email: str
    name: str | None
    role: Role

    @classmethod
    def of(cls, user: User) -> Me:
        return cls(
            user_id=user.user_id,
            tenant_id=user.tenant_id,
            email=user.email,
            name=user.name,
            role=user.role,
        )


class UserIn(BaseModel):
    email: str = Field(max_length=254, pattern=EMAIL)
    name: str | None = Field(None, max_length=200)
    role: Role
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)
    tenant_id: int | None = Field(None, gt=0, le=INT_MAX)


class UserOut(BaseModel):
    user_id: int
    tenant_id: int | None
    email: str
    name: str | None
    role: Role
    active: bool


class TenantOut(BaseModel):
    tenant_id: int
    name: str
    status: str
    city: str | None


class ImportResult(BaseModel):
    rows_in: int
    unparseable: int
    duplicates: int
    conflicting_duplicates: int
    negative: int
    resets: int
    gap_diffs: int
    future: int
    step_minutes: float
    hours_out: int
    partial_hours: int
    first_ts: str | None
    last_ts: str | None
