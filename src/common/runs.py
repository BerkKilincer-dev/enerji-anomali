"""Run labelling for hourly flags, shared by data reports, rules and metrics."""

from __future__ import annotations

import numpy as np


def runs(
    flags: np.ndarray, building: np.ndarray, max_gap: int = 0, hour: np.ndarray | None = None
) -> np.ndarray:
    """Run id per True hour (consecutive per building, gaps up to max_gap merged); -1 elsewhere.
    Rows are taken as consecutive hours unless `hour` gives each row's hour number."""
    idx = np.flatnonzero(flags)
    out = np.full(len(flags), -1, dtype=np.int64)
    if not len(idx):
        return out
    at = idx if hour is None else hour[idx]
    new = np.r_[True, (np.diff(at) > max_gap + 1) | (building[idx[1:]] != building[idx[:-1]])]
    out[idx] = np.cumsum(new) - 1
    return out
