from datetime import date

import pytest

from src.data.weather_api import ARCHIVE_URL, FORECAST_URL, Location, build_urls, parse_hourly


def test_location_key_and_validation():
    assert Location(41.0082, 28.9784, "Europe/Istanbul").weather_key == "geo:41.01,28.98"
    with pytest.raises(ValueError):
        Location(91, 0, "UTC")


def test_ranges_are_split_between_archive_and_forecast():
    loc = Location(41.0, 29.0, "Europe/Istanbul")
    today = date(2026, 10, 19)
    (old,) = build_urls(loc, date(2026, 1, 1), date(2026, 1, 31), today)
    assert old.startswith(ARCHIVE_URL)
    (recent,) = build_urls(loc, date(2026, 10, 15), date(2026, 10, 19), today)
    assert recent.startswith(FORECAST_URL) and "timezone=Europe%2FIstanbul" in recent
    archive, forecast = build_urls(loc, date(2025, 10, 19), date(2026, 10, 19), today)
    assert archive.startswith(ARCHIVE_URL) and "end_date=2026-10-14" in archive
    assert forecast.startswith(FORECAST_URL) and "start_date=2026-10-15" in forecast


def test_parse_hourly_converts_cloud_cover_to_oktas():
    payload = {
        "hourly": {
            "time": ["2026-10-01T00:00", "2026-10-01T01:00"],
            "temperature_2m": [12.5, None],
            "cloud_cover": [100, 50],
        }
    }
    df = parse_hourly(payload, "geo:41.00,29.00")
    assert df["cloud_cover"].tolist() == [8.0, 4.0]
    assert df["air_temp"].isna().tolist() == [False, True]
    with pytest.raises(ValueError, match="bad"):
        parse_hourly({"error": True, "reason": "bad"}, "k")
