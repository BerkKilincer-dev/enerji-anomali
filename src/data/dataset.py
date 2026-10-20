"""Build the modeling frame shared by ML, scoring and the notebooks."""

from __future__ import annotations

import logging

import pandas as pd

from src.config import Settings, get_settings
from src.data import download
from src.data.prepare import Tables, prepare
from src.data.split import Split, assign_splits
from src.data.validate import UNRELIABLE_QUALITY
from src.features.build_features import STATIC_COLUMNS, regularize

log = logging.getLogger(__name__)


def ensure_tables(settings: Settings | None = None) -> Tables:
    """Load processed tables, building them from raw sources on first use."""
    settings = settings or get_settings()
    if all((settings.processed_dir / f"{n}.parquet").exists() for n in Tables.FILES):
        return Tables.load(settings.processed_dir)
    log.info("processed data missing, rebuilding from raw sources")
    download.fetch_all(settings.raw_dir)
    tables = prepare(settings.raw_dir)
    tables.save(settings.processed_dir)
    return tables


def model_frame(tables: Tables, split: Split | None = None) -> pd.DataFrame:
    """Readings on a complete hourly grid, joined with building metadata, weather and split.

    anomaly is float32 with NaN for unlabeled hours, so comparisons never see pd.NA.
    Partial and merged hours count as missing.
    """
    buildings = tables.buildings.assign(split=assign_splits(tables.buildings, split))
    readings = tables.readings
    if "quality" in readings:
        unreliable = readings["quality"].isin(UNRELIABLE_QUALITY)
        readings = readings.assign(kwh=readings["kwh"].mask(unreliable)).drop(columns="quality")
    df = regularize(readings.assign(in_source=True))
    df["in_source"] = df["in_source"].eq(True)
    df["is_missing"] = df["kwh"].isna()
    df["anomaly"] = df["anomaly"].astype("float32")
    df = df.merge(buildings[["building_id", *STATIC_COLUMNS]], on="building_id", how="left")
    df = df.merge(tables.weather, on=["weather_key", "ts"], how="left")
    return df.sort_values(["building_id", "ts"], ignore_index=True)
