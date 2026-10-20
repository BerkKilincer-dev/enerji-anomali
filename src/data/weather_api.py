"""Hourly weather for any building location from Open-Meteo (free, no API key).

Training uses BDG2 site weather; live buildings use this client with their own
coordinates. Values come back in site local time and in BDG2 units.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from src.config import get_settings

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ARCHIVE_DELAY_DAYS = 5
HOURLY = {
    "temperature_2m": "air_temp",
    "dew_point_2m": "dew_temp",
    "wind_speed_10m": "wind_speed",
    "cloud_cover": "cloud_cover",
    "precipitation": "precip_1h",
    "pressure_msl": "sea_level_pressure",
}


@dataclass(frozen=True)
class Location:
    lat: float
    lng: float
    timezone: str

    def __post_init__(self) -> None:
        if not (-90 <= self.lat <= 90 and -180 <= self.lng <= 180):
            raise ValueError(f"invalid coordinates {self.lat}, {self.lng}")

    @property
    def weather_key(self) -> str:
        # Two decimals (~1 km) lets nearby buildings share one weather series.
        return f"geo:{self.lat:.2f},{self.lng:.2f}"

    @classmethod
    def default(cls) -> Location:
        s = get_settings()
        return cls(s.default_lat, s.default_lng, s.default_timezone)


def build_urls(loc: Location, start: date, end: date, today: date | None = None) -> list[str]:
    """Archive API for days old enough to be archived, the forecast API for the rest."""
    archived_until = (today or date.today()) - timedelta(days=ARCHIVE_DELAY_DAYS)
    ranges = []
    if start <= archived_until:
        ranges.append((ARCHIVE_URL, start, min(end, archived_until)))
    if end > archived_until:
        ranges.append((FORECAST_URL, max(start, archived_until + timedelta(days=1)), end))
    return [f"{base}?{urllib.parse.urlencode(_params(loc, a, b))}" for base, a, b in ranges]


def _params(loc: Location, start: date, end: date) -> dict:
    return {
        "latitude": f"{loc.lat:.4f}",
        "longitude": f"{loc.lng:.4f}",
        "hourly": ",".join(HOURLY),
        "timezone": loc.timezone,
        "wind_speed_unit": "ms",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }


def parse_hourly(payload: dict, weather_key: str) -> pd.DataFrame:
    hourly = payload.get("hourly") or {}
    if "time" not in hourly:
        raise ValueError(payload.get("reason", "unexpected Open-Meteo response"))
    df = pd.DataFrame({"ts": pd.to_datetime(hourly["time"])})
    for api_name, column in HOURLY.items():
        df[column] = pd.to_numeric(pd.Series(hourly.get(api_name)), errors="coerce").astype(
            "float32"
        )
    # Open-Meteo reports cloud cover in %, BDG2 in oktas.
    df["cloud_cover"] = (df["cloud_cover"] / 100 * 8).round()
    df.insert(0, "weather_key", weather_key)
    return df.drop_duplicates("ts", keep="last").reset_index(drop=True)


def fetch(loc: Location, start: date, end: date, timeout: float = 30) -> pd.DataFrame:
    frames = []
    for url in build_urls(loc, start, end):
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            frames.append(parse_hourly(json.load(resp), loc.weather_key))
    return pd.concat(frames, ignore_index=True)
