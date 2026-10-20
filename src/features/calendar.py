"""Public holidays per country, including the Turkish half-day eve before each Bayram."""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import holidays
import numpy as np
import pandas as pd

NIGHT_HOURS = range(0, 6)
EVE_HOLIDAYS = {"TR": ("Eid al-Fitr", "Eid al-Adha")}
EVE_FROM_HOUR = 13


@lru_cache(maxsize=64)
def _days(country: str, year: int) -> tuple[frozenset[date], frozenset[date]]:
    """(full holiday dates, half-day eve dates) for one country and year."""
    try:
        calendar = holidays.country_holidays(
            country, years=[year - 1, year, year + 1], language="en_US"
        )
    except NotImplementedError:
        return frozenset(), frozenset()
    full = frozenset(d for d in calendar if d.year == year)
    eves = set()
    for name in EVE_HOLIDAYS.get(country, ()):
        for day in calendar.get_named(name, lookup="exact"):
            if day - timedelta(days=1) not in calendar:
                eves.add(day - timedelta(days=1))
    return full, frozenset(d for d in eves if d.year == year)


def holiday_flags(ts: pd.Series, country: pd.Series) -> np.ndarray:
    """1 on public holidays and on Turkish Bayram eves from 13:00, else 0."""
    out = np.zeros(len(ts), dtype="int8")
    days = ts.to_numpy().astype("datetime64[D]")
    years, hours = ts.dt.year.to_numpy(), ts.dt.hour.to_numpy()
    codes = country.fillna("").astype(str).str.upper().to_numpy()
    for code in filter(None, pd.unique(codes)):
        for year in pd.unique(years[codes == code]):
            full, eves = (
                np.array(sorted(d), dtype="datetime64[D]") for d in _days(code, int(year))
            )
            rows = (codes == code) & (years == year)
            out[rows & np.isin(days, full)] = 1
            out[rows & np.isin(days, eves) & (hours >= EVE_FROM_HOUR)] = 1
    return out
