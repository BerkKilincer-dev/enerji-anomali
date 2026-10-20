"""Label-free detectors: expected-consumption regression and Isolation Forest."""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.model_selection import GroupKFold

from src.config import SEED
from src.features.build_features import HourlyGrid, grid_parts
from src.models.boosting import datasets, fit_lightgbm

EXPECTED_PARAMS = {
    "objective": "l1",
    "metric": "l1",
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_child_samples": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbose": -1,
    "seed": SEED,
}
SCALE_WINDOW = 336
MIN_SCALE_HOURS = 48
SCALE_FLOOR = 0.05
IF_FEATURES = [
    "diff_1",
    "diff_24",
    "diff_168",
    "z_24",
    "z_168",
    "resid_hw",
    "resid_hd",
    "level_ratio",
    "flat_run",
    "is_zero",
    "missing_24",
    "hour",
    "is_weekend",
]


def train_expected(
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    x_val: pd.DataFrame,
    y_val: np.ndarray,
    rounds: int = 2000,
) -> lgb.Booster:
    """Predict log1p(kWh) from calendar, weather and robust baselines (L1 = robust to anomalies)."""
    return fit_lightgbm(EXPECTED_PARAMS, *datasets(x_train, y_train, x_val, y_val), rounds)


def out_of_fold_expected(
    x: pd.DataFrame,
    y: np.ndarray,
    groups: np.ndarray,
    x_val: pd.DataFrame,
    y_val: np.ndarray,
    folds: int = 5,
) -> np.ndarray:
    """Expected consumption for training rows from models that never saw that building."""
    oof = np.full(len(x), np.nan)
    for fit_idx, pred_idx in GroupKFold(n_splits=folds).split(x, y, groups):
        model = train_expected(x.iloc[fit_idx], y[fit_idx], x_val, y_val)
        oof[pred_idx] = model.predict(x.iloc[pred_idx], num_iteration=model.best_iteration)
    return oof


def residual_score(frame: pd.DataFrame, residual: np.ndarray) -> np.ndarray:
    """|residual| over the building's own recent residual spread; NaN before 48 past hours."""
    absres = pd.Series(np.abs(residual), index=frame.index)
    score = pd.Series(np.nan, index=frame.index)
    for rows in grid_parts(frame):
        grid = HourlyGrid(frame.loc[rows])
        wide = grid.wide(absres.loc[rows])
        scale = wide.shift(1).rolling(SCALE_WINDOW, min_periods=MIN_SCALE_HOURS).median()
        score.loc[rows] = grid.long(wide / (scale + SCALE_FLOOR))
    return score.to_numpy()


def train_isolation_forest(
    x: pd.DataFrame, max_rows: int = 200_000, seed: int = SEED
) -> IsolationForest:
    rng = np.random.default_rng(seed)
    rows = rng.choice(len(x), size=min(max_rows, len(x)), replace=False)
    model = IsolationForest(n_estimators=200, random_state=seed, n_jobs=-1)
    return model.fit(x[IF_FEATURES].iloc[rows].astype("float32").fillna(0).to_numpy())


def isolation_score(model: IsolationForest, x: pd.DataFrame) -> np.ndarray:
    return -model.score_samples(x[IF_FEATURES].astype("float32").fillna(0).to_numpy())
