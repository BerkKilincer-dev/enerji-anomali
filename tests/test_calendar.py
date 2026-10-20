import pandas as pd

from src.features.calendar import holiday_flags


def flags(stamps, country="TR"):
    ts = pd.Series(pd.to_datetime(stamps))
    return holiday_flags(ts, pd.Series([country] * len(ts))).tolist()


def test_fixed_holidays():
    assert flags(["2026-10-29 10:00", "2026-10-28 10:00"]) == [1, 0]
    assert flags(["2016-07-04 12:00"], "US") == [1]


def test_bayram_eve_is_a_half_day():
    # Ramazan Bayrami 2026 starts on March 20, so the eve is March 19 from 13:00.
    assert flags(["2026-03-19 12:00", "2026-03-19 13:00", "2026-03-20 09:00"]) == [0, 1, 1]


def test_unknown_or_missing_country_has_no_holidays():
    ts = pd.Series(pd.to_datetime(["2026-01-01 10:00", "2026-01-01 10:00"]))
    assert holiday_flags(ts, pd.Series(["XX", None])).tolist() == [0, 0]
