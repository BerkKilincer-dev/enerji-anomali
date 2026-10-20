import pandas as pd
import pytest

from src.data.validate import normalize_readings

NOW = pd.Timestamp("2026-10-19 12:00", tz="Europe/Istanbul")


def raw(ts, values):
    return pd.DataFrame({"ts": ts, "value": values})


def test_quarter_hours_sum_to_hourly_kwh_with_quality():
    ts = pd.date_range("2026-10-01 00:00", periods=7, freq="15min").strftime("%Y-%m-%d %H:%M")
    hourly, report = normalize_readings(raw(ts, [1, 1, 1, 1, 2, 2, 2]), "Europe/Istanbul", now=NOW)
    assert hourly["kwh"].tolist() == [4.0, 6.0]
    assert hourly["quality"].tolist() == ["ok", "partial"]
    assert report.step_minutes == 15 and report.hours_out == 2


def test_cumulative_wh_register_with_reset_and_gap():
    ts = pd.date_range("2026-10-01 00:00", periods=6, freq="h").strftime("%Y-%m-%d %H:%M")
    values = [1000, 3000, 6000, 500, None, 2500]
    hourly, report = normalize_readings(
        raw(ts, values), "Europe/Istanbul", unit="wh", cumulative=True, now=NOW
    )
    # Each diff belongs to the earlier reading's hour; the reset counts from zero.
    assert hourly["kwh"].round(3).tolist()[:3] == [2.0, 3.0, 0.5]
    assert hourly["kwh"].isna().sum() >= 2
    assert report.resets == 1


def test_offsets_are_converted_to_building_local_time():
    hourly, _ = normalize_readings(
        raw(["2026-10-01T09:00:00Z", "2026-10-01T10:00:00Z"], [1, 2]), "Europe/Istanbul", now=NOW
    )
    assert hourly["ts"].dt.hour.tolist() == [12, 13]


def test_naive_times_follow_source_tz():
    hourly, _ = normalize_readings(
        raw(["2026-10-01 09:00", "2026-10-01 10:00"], [1, 2]),
        "Europe/Istanbul",
        source_tz="UTC",
        now=NOW,
    )
    assert hourly["ts"].dt.hour.tolist() == [12, 13]


def test_dst_fall_back_keeps_both_repeated_hours():
    ts = [
        "2026-10-25 01:00+02:00",
        "2026-10-25 02:00+02:00",
        "2026-10-25 02:00+01:00",
        "2026-10-25 03:00+01:00",
    ]
    hourly, report = normalize_readings(
        raw(ts, [1, 1, 1, 1]), "Europe/Berlin", now=pd.Timestamp("2026-11-01", tz="UTC")
    )
    assert report.unparseable == 0
    assert hourly.loc[hourly["ts"] == pd.Timestamp("2026-10-25 02:00"), "kwh"].item() == 2.0


def test_duplicates_negatives_and_future_rows():
    ts = [
        "2026-10-01 00:00",
        "2026-10-01 00:00",
        "2026-10-01 01:00",
        "2026-10-01 01:00",
        "2026-10-01 02:00",
        "2027-01-01 00:00",
        "not a date",
    ]
    hourly, report = normalize_readings(raw(ts, [1, 1, 2, 3, -5, 9, 1]), "Europe/Istanbul", now=NOW)
    assert (
        report.duplicates,
        report.conflicting_duplicates,
        report.future,
        report.unparseable,
        report.negative,
    ) == (1, 1, 1, 1, 1)
    assert hourly["kwh"].tolist()[:2] == [1.0, 3.0]
    assert pd.isna(hourly["kwh"].iloc[2])


def test_rejects_coarse_steps_unknown_units_and_mixed_offsets():
    daily = pd.date_range("2026-10-01", periods=3, freq="D").strftime("%Y-%m-%d")
    with pytest.raises(ValueError, match="hourly or finer"):
        normalize_readings(raw(daily, [1, 2, 3]), "Europe/Istanbul", now=NOW)
    with pytest.raises(ValueError, match="unknown unit"):
        normalize_readings(raw(daily, [1, 2, 3]), "Europe/Istanbul", unit="therm")
    with pytest.raises(ValueError):
        normalize_readings(
            raw(["2026-10-01 00:00+03:00", "2026-10-01 01:00"], [1, 2]), "Europe/Istanbul", now=NOW
        )
