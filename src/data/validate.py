"""Turn a raw customer meter export into clean hourly kWh in building local time.

Conventions: a value stamped `ts` covers the interval that starts at `ts`; cumulative
registers are differenced and the difference is stamped at the earlier reading.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

UNIT_TO_KWH = {"wh": 0.001, "kwh": 1.0, "mwh": 1000.0, "kbtu": 0.2931}
FUTURE_TOLERANCE = pd.Timedelta(hours=1)
# Hours with fewer or more meter intervals than expected do not hold a true hourly total.
UNRELIABLE_QUALITY = ("partial", "merged")


@dataclass
class ImportReport:
    rows_in: int = 0
    unparseable: int = 0
    duplicates: int = 0
    conflicting_duplicates: int = 0
    negative: int = 0
    resets: int = 0
    gap_diffs: int = 0
    future: int = 0
    step_minutes: float = 0.0
    hours_out: int = 0
    partial_hours: int = 0
    first_ts: str | None = None
    last_ts: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


OFFSET_SUFFIX = r"\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?\s*(?:Z|[+-]\d{2}:?\d{2})$"


def _instants(ts: pd.Series, timezone: str, source_tz: str | None) -> pd.Series:
    """UTC instants; naive timestamps are read in source_tz, else in the building timezone."""
    if isinstance(ts.dtype, pd.DatetimeTZDtype):
        return ts.dt.tz_convert("UTC")
    if not pd.api.types.is_datetime64_dtype(ts):
        text = ts.astype(str).str.strip()
        has_offset = text.str.contains(OFFSET_SUFFIX, regex=True)
        if has_offset.all():
            return pd.to_datetime(text, utc=True, errors="coerce", format="mixed")
        if has_offset.any():
            raise ValueError("timestamps mix values with and without a UTC offset")
        ts = pd.to_datetime(text, errors="coerce", format="mixed")
    zone = source_tz or timezone
    try:
        # A repeated wall-clock hour at the autumn DST change is resolved by its order.
        local = ts.dt.tz_localize(zone, ambiguous="infer", nonexistent="NaT")
    except ValueError:
        local = ts.dt.tz_localize(zone, ambiguous="NaT", nonexistent="NaT")
    return local.dt.tz_convert("UTC")


def _interval_values(
    frame: pd.DataFrame, cumulative: bool, step: pd.Timedelta, report: ImportReport
) -> pd.Series:
    if not cumulative:
        values = frame["value"].where(frame["value"] >= 0)
        report.negative = int((frame["value"] < 0).sum())
        return values
    diff = frame["value"].diff()
    reset = diff < 0
    report.resets = int(reset.sum())
    # After a reset the register restarted from zero, so its value is the usage since then.
    diff = diff.where(~reset, frame["value"])
    gap = frame["utc"].diff() > step * 1.5
    report.gap_diffs = int(gap.sum())
    return diff.where(~gap).shift(-1)


def normalize_readings(
    raw: pd.DataFrame,
    timezone: str,
    unit: str = "kwh",
    cumulative: bool = False,
    source_tz: str | None = None,
    now: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, ImportReport]:
    """`raw` has columns ts and value; returns hourly ts, kwh, meter_raw, quality."""
    factor = UNIT_TO_KWH.get(unit.lower())
    if factor is None:
        raise ValueError(f"unknown unit {unit!r}, expected one of {sorted(UNIT_TO_KWH)}")
    report = ImportReport(rows_in=len(raw))
    frame = pd.DataFrame(
        {
            "utc": _instants(raw["ts"], timezone, source_tz),
            "raw": pd.to_numeric(raw["value"], errors="coerce").astype("float64"),
        }
    )
    frame["value"] = frame["raw"] * factor
    unparseable = frame["utc"].isna()
    now_utc = pd.Timestamp.now(tz="UTC") if now is None else now.tz_convert("UTC")
    future = frame["utc"] > now_utc + FUTURE_TOLERANCE
    report.unparseable, report.future = int(unparseable.sum()), int(future.sum())
    frame = frame[~unparseable & ~future]

    exact = frame.duplicated(["utc", "raw"])
    report.duplicates = int(exact.sum())
    frame = frame[~exact]
    conflicts = frame.duplicated("utc", keep="last")
    report.conflicting_duplicates = int(conflicts.sum())
    frame = frame[~conflicts].sort_values("utc", ignore_index=True)
    if frame.empty:
        return pd.DataFrame(columns=["ts", "kwh", "meter_raw", "quality"]), report

    step = frame["utc"].diff().median() if len(frame) > 1 else pd.Timedelta(hours=1)
    if step > pd.Timedelta(hours=1):
        raise ValueError(f"readings must be hourly or finer, got a {step} step")
    report.step_minutes = step / pd.Timedelta(minutes=1)
    frame["kwh"] = _interval_values(frame, cumulative, step, report)

    frame["ts"] = frame["utc"].dt.tz_convert(timezone).dt.tz_localize(None).dt.floor("h")
    per_hour = round(pd.Timedelta(hours=1) / step)
    g = frame.groupby("ts")
    hourly = pd.DataFrame(
        {
            "kwh": g["kwh"].sum(min_count=1).round(4),
            "meter_raw": g["raw"].last(),
            "count": g["kwh"].count(),
        }
    )
    hourly["quality"] = np.select(
        [hourly["kwh"].isna(), hourly["count"] < per_hour, hourly["count"] > per_hour],
        ["missing", "partial", "merged"],
        "ok",
    )
    hourly = hourly.drop(columns="count").reset_index()
    report.hours_out = len(hourly)
    report.partial_hours = int(hourly["quality"].isin(["missing", "partial"]).sum())
    report.first_ts, report.last_ts = (str(t) for t in (hourly["ts"].min(), hourly["ts"].max()))
    return hourly, report
