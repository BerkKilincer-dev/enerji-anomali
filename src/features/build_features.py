"""Özellik çıkarma. Sahibi: Veri mühendisi + ML geliştirici.

Önerilen özellikler: saat, haftanın günü, hafta sonu, sıcaklık,
lag_1 / lag_24 / lag_168, rolling_mean_24, rolling_std_24 (bina bazında hesaplanır).
"""

import pandas as pd


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["hour"] = df["timestamp"].dt.hour
    df["dayofweek"] = df["timestamp"].dt.dayofweek
    df["is_weekend"] = df["dayofweek"] >= 5
    return df


def add_lag_features(df: pd.DataFrame, col: str = "meter_reading") -> pd.DataFrame:
    df = df.sort_values(["building_id", "timestamp"]).copy()
    g = df.groupby("building_id")[col]
    for lag in (1, 24, 168):
        df[f"lag_{lag}"] = g.shift(lag)
    df["rolling_mean_24"] = g.transform(lambda s: s.shift(1).rolling(24, min_periods=6).mean())
    df["rolling_std_24"] = g.transform(lambda s: s.shift(1).rolling(24, min_periods=6).std())
    return df
