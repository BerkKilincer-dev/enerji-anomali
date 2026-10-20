"""Past-only feature engineering shared by training and live scoring.

Features at hour t use the reading at t and anything before it, never after.
Input: long frame with no hourly gaps inside a building; call `regularize`
first when that is not guaranteed. Buildings may cover different periods
(historical data next to a new customer).
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from src.features.calendar import holiday_flags

EPS = 1e-3
COMFORT_TEMP_C = 18.0
PRIMARY_USES = (
    "Education",
    "Entertainment/public assembly",
    "Food sales and service",
    "Healthcare",
    "Lodging/residential",
    "Manufacturing/industrial",
    "Office",
    "Other",
    "Parking",
    "Public services",
    "Religious worship",
    "Retail",
    "Services",
    "Technology/science",
    "Utility",
    "Warehouse/storage",
)
STATIC_COLUMNS = (
    "site_id",
    "weather_key",
    "primary_use",
    "square_feet",
    "year_built",
    "country",
    "split",
)
WEATHER_COLUMNS = ("air_temp", "dew_temp", "wind_speed", "cloud_cover")
HISTORY_HOURS = 4 * 168  # longest lookback of any feature (median_672, hw_median)

CALENDAR = ["hour", "dayofweek", "is_weekend", "is_holiday"]
WEATHER = [
    *WEATHER_COLUMNS,
    "heating_deg",
    "cooling_deg",
    "temp_mean_168",
    "temp_anom",
    "dew_spread",
]
BUILDING = ["log_sqft", "year_built", "primary_use"]
BASELINE = ["hw_median", "hd_median", "median_168", "median_672", "mean_168", "std_168"]
RECENT = [
    "log_kwh",
    "lag_1",
    "lag_2",
    "lag_3",
    "lag_24",
    "lag_168",
    "diff_1",
    "diff_24",
    "diff_168",
    "mean_24",
    "std_24",
    "min_24",
    "max_24",
    "z_24",
    "z_168",
    "resid_hw",
    "resid_hd",
    "level_ratio",
    "is_zero",
    "flat_run",
    "missing_24",
]
# No current value or short lags, so a sustained anomaly cannot leak into its own baseline.
EXPECTED_FEATURES = [*CALENDAR, *WEATHER, *BUILDING, *BASELINE]
DETECTOR_FEATURES = [*CALENDAR, *WEATHER, *BUILDING, *BASELINE, *RECENT]
NO_WEATHER_FEATURES = [f for f in DETECTOR_FEATURES if f not in WEATHER]


def regularize(df: pd.DataFrame) -> pd.DataFrame:
    """Fill hourly gaps inside each building's own period, carrying static columns."""
    span = df.groupby("building_id")["ts"].agg(["min", "max"]).sort_index()
    hours = ((span["max"] - span["min"]) // pd.Timedelta(hours=1)).astype("int64").to_numpy() + 1
    offsets = np.arange(hours.sum()) - np.repeat(np.cumsum(hours) - hours, hours)
    grid = pd.MultiIndex.from_arrays(
        [
            np.repeat(span.index.to_numpy(), hours),
            np.repeat(span["min"].to_numpy(), hours) + offsets * np.timedelta64(1, "h"),
        ],
        names=["building_id", "ts"],
    )
    out = df.set_index(["building_id", "ts"]).reindex(grid).reset_index()
    static = [c for c in STATIC_COLUMNS if c in out.columns]
    if static:
        out[static] = out.groupby("building_id")[static].transform(lambda s: s.ffill().bfill())
    return out


class HourlyGrid:
    """Moves a long column to a (time x building) frame and back."""

    def __init__(self, df: pd.DataFrame):
        self.n_buildings = df["building_id"].nunique()
        self.n_hours = len(df) // self.n_buildings
        if self.n_buildings * self.n_hours != len(df):
            raise ValueError("buildings do not share one hourly grid")

    def wide(self, values: pd.Series | np.ndarray) -> pd.DataFrame:
        arr = np.asarray(values, dtype="float64").reshape(self.n_buildings, self.n_hours)
        return pd.DataFrame(arr.T)

    def long(self, wide: pd.DataFrame | np.ndarray) -> np.ndarray:
        return np.asarray(wide, dtype="float64").T.reshape(-1)


def _nanmedian(frames: list[pd.DataFrame]) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(np.stack([f.to_numpy() for f in frames]), axis=0)


def _run_length(same: np.ndarray) -> np.ndarray:
    """Consecutive True count per column, reset to 0 on False."""
    run = np.zeros_like(same, dtype="float64")
    for t in range(1, len(same)):
        run[t] = np.where(same[t], run[t - 1] + 1, 0)
    return run


def grid_parts(df: pd.DataFrame) -> list[pd.Index]:
    """Row labels of each group of buildings that cover the same hours, building by building."""
    ordered = df.sort_values(["building_id", "ts"], kind="stable")
    ts = ordered.groupby("building_id", sort=False)["ts"]
    start, hours = ts.transform("min"), ts.transform("size")
    if (ordered["ts"] - start != pd.to_timedelta(ts.cumcount(), unit="h")).any():
        raise ValueError("hourly gaps inside a building; call regularize()")
    return [rows.index for _, rows in ordered.groupby([start, hours], sort=False)]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Feature frame aligned row-for-row with `df`."""
    return pd.concat([_grid_features(df.loc[rows]) for rows in grid_parts(df)]).loc[df.index]


def _grid_features(df: pd.DataFrame) -> pd.DataFrame:
    g = HourlyGrid(df)
    out = pd.DataFrame(index=df.index)
    feat: dict[str, np.ndarray] = {}

    x = g.wide(np.log1p(df["kwh"].clip(lower=0)))
    past = x.shift(1)
    for lag in (1, 2, 3, 24, 168):
        feat[f"lag_{lag}"] = g.long(x.shift(lag))
    feat["log_kwh"] = g.long(x)
    for lag in (1, 24, 168):
        feat[f"diff_{lag}"] = feat["log_kwh"] - feat[f"lag_{lag}"]

    roll24, roll168 = past.rolling(24, min_periods=12), past.rolling(168, min_periods=48)
    mean_24, std_24 = roll24.mean(), roll24.std()
    mean_168, std_168 = roll168.mean(), roll168.std()
    feat.update(
        mean_24=g.long(mean_24),
        std_24=g.long(std_24),
        min_24=g.long(roll24.min()),
        max_24=g.long(roll24.max()),
        mean_168=g.long(mean_168),
        std_168=g.long(std_168),
        median_168=g.long(roll168.median()),
        median_672=g.long(past.rolling(HISTORY_HOURS, min_periods=168).median()),
        z_24=g.long((x - mean_24) / (std_24 + EPS)),
        z_168=g.long((x - mean_168) / (std_168 + EPS)),
        hw_median=g.long(
            _nanmedian([x.shift(168 * k) for k in range(1, HISTORY_HOURS // 168 + 1)])
        ),
        hd_median=g.long(_nanmedian([x.shift(24 * k) for k in range(1, 8)])),
    )
    feat["resid_hw"] = feat["log_kwh"] - feat["hw_median"]
    feat["resid_hd"] = feat["log_kwh"] - feat["hd_median"]
    feat["level_ratio"] = feat["log_kwh"] - feat["median_672"]

    kwh = g.wide(df["kwh"])
    same = (kwh.diff().abs() < 1e-9).to_numpy()
    feat["flat_run"] = g.long(_run_length(same))
    feat["is_zero"] = np.where(np.isnan(feat["log_kwh"]), np.nan, (feat["log_kwh"] == 0) * 1.0)
    feat["missing_24"] = g.long(past.isna().rolling(24, min_periods=1).sum())

    for name, values in feat.items():
        out[name] = values.astype("float32")

    out["hour"] = df["ts"].dt.hour.astype("int8")
    out["dayofweek"] = df["ts"].dt.dayofweek.astype("int8")
    out["is_weekend"] = (out["dayofweek"] >= 5).astype("int8")
    country = df["country"] if "country" in df else pd.Series("", index=df.index)
    out["is_holiday"] = holiday_flags(df["ts"], country)

    for col in WEATHER_COLUMNS:
        out[col] = df[col].astype("float32") if col in df else np.float32(np.nan)
    temp = g.wide(out["air_temp"])
    out["heating_deg"] = (COMFORT_TEMP_C - out["air_temp"]).clip(lower=0)
    out["cooling_deg"] = (out["air_temp"] - COMFORT_TEMP_C).clip(lower=0)
    out["temp_mean_168"] = g.long(temp.shift(1).rolling(168, min_periods=24).mean()).astype(
        "float32"
    )
    out["temp_anom"] = out["air_temp"] - out["temp_mean_168"]
    out["dew_spread"] = out["air_temp"] - out["dew_temp"]

    out["log_sqft"] = (
        np.log1p(df["square_feet"]).astype("float32") if "square_feet" in df else np.nan
    )
    out["year_built"] = (
        pd.to_numeric(df["year_built"], errors="coerce").astype("float32")
        if "year_built" in df
        else np.float32(np.nan)
    )
    uses = df["primary_use"] if "primary_use" in df else pd.Series(index=df.index, dtype=object)
    out["primary_use"] = pd.Categorical(uses, categories=PRIMARY_USES)
    return out
