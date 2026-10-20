"""Shared LightGBM training: dataset pairs and early stopping on validation buildings."""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd

EARLY_STOP = 100


def datasets(
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    x_val: pd.DataFrame,
    y_val: np.ndarray,
    reusable: bool = False,
) -> tuple[lgb.Dataset, lgb.Dataset]:
    # A reusable pair keeps raw data and unfiltered bins so tuning trials can share it.
    params = {"feature_pre_filter": False} if reusable else None
    train = lgb.Dataset(x_train, y_train, params=params, free_raw_data=not reusable)
    return train, lgb.Dataset(x_val, y_val, reference=train, params=params)


def fit_lightgbm(params: dict, train: lgb.Dataset, val: lgb.Dataset, rounds: int) -> lgb.Booster:
    """Train until validation stops improving for EARLY_STOP rounds."""
    return lgb.train(
        params,
        train,
        rounds,
        valid_sets=[val],
        callbacks=[lgb.early_stopping(EARLY_STOP, verbose=False), lgb.log_evaluation(0)],
    )
