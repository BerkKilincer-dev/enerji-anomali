"""Request and response bodies. Timestamps are building local time without an offset."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, Field, field_validator


class Building(BaseModel):
    building_id: int
    external_id: str
    name: str | None
    source: str
    primary_use: str | None
    square_feet: float | None
    timezone: str
    country: str | None
    lat: float | None
    lng: float | None
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

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str | None) -> str | None:
        if value is not None and value not in available_timezones():
            raise ValueError(f"unknown timezone {value!r}")
        return value


class Created(BaseModel):
    id: int


class FeedbackIn(BaseModel):
    verdict: Literal["confirmed", "false_alarm", "planned"]
    note: str | None = Field(None, max_length=1000)
    created_by: str | None = Field(None, max_length=100)


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
