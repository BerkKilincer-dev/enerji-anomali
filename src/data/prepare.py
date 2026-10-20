"""Clean and merge raw sources into processed parquet tables.

Usage: python -m src.data.prepare
Writes buildings.parquet, weather.parquet and readings.parquet to data/processed.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import get_settings
from src.data.sources import BDG2_METADATA, BDG2_WEATHER, LEAD
from src.data.validate import UNIT_TO_KWH

log = logging.getLogger(__name__)

KBTU_TO_KWH = UNIT_TO_KWH["kbtu"]
KBTU_SITE_IDS = frozenset({0})  # ASHRAE site 0 electricity is reported in kBTU
PERIOD = pd.date_range("2016-01-01 00:00", "2016-12-31 23:00", freq="h")
WEATHER_FFILL_LIMIT = 3
BDG2_WEATHER_NAMES = {
    "airTemperature": "air_temp",
    "dewTemperature": "dew_temp",
    "windSpeed": "wind_speed",
    "cloudCoverage": "cloud_cover",
    "precipDepth1HR": "precip_1h",
    "seaLvlPressure": "sea_level_pressure",
}
WEATHER_COLUMNS = list(BDG2_WEATHER_NAMES.values())


@dataclass(frozen=True)
class Tables:
    buildings: pd.DataFrame
    weather: pd.DataFrame
    readings: pd.DataFrame

    FILES = ("buildings", "weather", "readings")

    def save(self, out_dir: Path) -> dict[str, Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = {}
        for name in self.FILES:
            path = out_dir / f"{name}.parquet"
            getattr(self, name).to_parquet(path, index=False)
            paths[name] = path
        return paths

    @classmethod
    def load(cls, out_dir: Path) -> Tables:
        return cls(*(pd.read_parquet(out_dir / f"{name}.parquet") for name in cls.FILES))


def read_lead(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.rename(columns={"timestamp": "ts", "meter_reading": "meter_raw"})
    if df.duplicated(["building_id", "ts"]).any():
        raise ValueError("duplicate (building_id, ts) rows in LEAD")
    return df


def country_for_timezone(tz: str) -> str | None:
    if tz.startswith("US/"):
        return "US"
    return {"Europe/London": "GB", "Europe/Dublin": "IE", "Europe/Istanbul": "TR"}.get(tz)


def build_buildings(meta: pd.DataFrame, building_ids: np.ndarray) -> pd.DataFrame:
    m = meta[meta["building_id_kaggle"].isin(building_ids)]
    if len(m) != len(building_ids):
        raise ValueError(f"metadata matched {len(m)} of {len(building_ids)} buildings")
    out = pd.DataFrame(
        {
            "building_id": m["building_id_kaggle"].astype("int32"),
            "external_id": m["building_id"].astype(str),
            "source": "lead",
            "site_id": m["site_id_kaggle"].astype("int16"),
            "weather_key": m["site_id"].astype(str),
            "primary_use": m["primaryspaceusage"].astype(str),
            "sub_use": m["sub_primaryspaceusage"].astype(str),
            "square_feet": m["sqft"].astype("float64"),
            "year_built": m["yearbuilt"].astype("Int16"),
            "timezone": m["timezone"].astype(str),
            "country": m["timezone"].map(country_for_timezone),
            "lat": m["lat"].astype("float64"),
            "lng": m["lng"].astype("float64"),
        }
    )
    return out.sort_values("building_id").reset_index(drop=True)


def build_weather(weather: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    # BDG2 weather is in site local time, same clock as the meter readings.
    w = weather.rename(columns={"site_id": "weather_key", "timestamp": "ts", **BDG2_WEATHER_NAMES})
    w = w[w["weather_key"].isin(keys)][["weather_key", "ts", *WEATHER_COLUMNS]]
    frames = []
    for key, part in w.groupby("weather_key", sort=True):
        part = part.drop_duplicates("ts").set_index("ts").reindex(PERIOD)
        part = part.drop(columns="weather_key").ffill(limit=WEATHER_FFILL_LIMIT)
        frames.append(part.rename_axis("ts").reset_index().assign(weather_key=key))
    out = pd.concat(frames, ignore_index=True)
    out[WEATHER_COLUMNS] = out[WEATHER_COLUMNS].astype("float32")
    return out[["weather_key", "ts", *WEATHER_COLUMNS]]


def build_readings(lead: pd.DataFrame, buildings: pd.DataFrame) -> pd.DataFrame:
    """Rows exactly as received; the hourly grid is completed later by model_frame."""
    df = lead.sort_values(["building_id", "ts"], ignore_index=True)
    raw = df["meter_raw"].where(df["meter_raw"] >= 0)
    site = df["building_id"].map(buildings.set_index("building_id")["site_id"])
    factor = np.where(site.isin(KBTU_SITE_IDS), KBTU_TO_KWH, 1.0)
    return pd.DataFrame(
        {
            "building_id": df["building_id"].astype("int32"),
            "ts": df["ts"],
            "kwh": (raw * factor).astype("float32"),
            "meter_raw": raw.astype("float32"),
            "anomaly": df["anomaly"].astype("Int8"),
            "source": "lead",
        }
    )


def prepare(raw_dir: Path) -> Tables:
    lead = read_lead(raw_dir / LEAD.filename)
    meta = pd.read_csv(raw_dir / BDG2_METADATA.filename, low_memory=False)
    weather = pd.read_csv(raw_dir / BDG2_WEATHER.filename, parse_dates=["timestamp"])

    buildings = build_buildings(meta, lead["building_id"].unique())
    return Tables(
        buildings=buildings,
        weather=build_weather(weather, sorted(buildings["weather_key"].unique())),
        readings=build_readings(lead, buildings),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()
    tables = prepare(settings.raw_dir)
    for name, path in tables.save(settings.processed_dir).items():
        print(f"{name}: {len(getattr(tables, name)):,} rows -> {path}")


if __name__ == "__main__":
    main()
