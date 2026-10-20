"""Supervised detectors: LightGBM (main model) and Random Forest (baseline)."""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from src.config import SEED
from src.models.boosting import datasets, fit_lightgbm

LGB_PARAMS = {
    "objective": "binary",
    "metric": "average_precision",
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_child_samples": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbose": -1,
    "seed": SEED,
}


def train_lightgbm(
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    x_val: pd.DataFrame,
    y_val: np.ndarray,
    params: dict | None = None,
    rounds: int = 3000,
) -> lgb.Booster:
    """Binary anomaly classifier."""
    pair = datasets(x_train, y_train, x_val, y_val)
    return fit_lightgbm({**LGB_PARAMS, **(params or {})}, *pair, rounds)


def rf_matrix(x: pd.DataFrame) -> np.ndarray:
    """Random Forest needs plain numbers: categories to codes, NaN to a sentinel."""
    x = x.copy()
    for col in x.select_dtypes("category"):
        x[col] = x[col].cat.codes
    return x.astype("float32").fillna(-999).to_numpy()


def train_random_forest(
    x: pd.DataFrame, y: np.ndarray, max_rows: int = 300_000, seed: int = SEED
) -> RandomForestClassifier:
    """Default-style forest on a row sample; full data does not fit RF's memory budget."""
    rng = np.random.default_rng(seed)
    rows = rng.choice(len(x), size=min(max_rows, len(x)), replace=False)
    model = RandomForestClassifier(
        n_estimators=200, min_samples_leaf=5, n_jobs=-1, random_state=seed
    )
    return model.fit(rf_matrix(x.iloc[rows]), y[rows])
