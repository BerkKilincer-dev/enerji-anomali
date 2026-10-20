"""Hourly, event-level and day-level metrics for anomaly detectors."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    precision_recall_curve,
    precision_recall_fscore_support,
    roc_auc_score,
)

from src.common.runs import runs

HOURS_PER_WEEK = 168


def hour_index(ts) -> np.ndarray:
    """Whole hours since the epoch, so metrics on a subset of rows still see its gaps."""
    return np.asarray(ts, dtype="datetime64[h]").astype(np.int64)


def pr_auc(y: np.ndarray, score: np.ndarray) -> float:
    return float(average_precision_score(y, np.nan_to_num(score, nan=0.0)))


def ranking(y: np.ndarray, score: np.ndarray) -> dict:
    return {"pr_auc": pr_auc(y, score), "roc_auc": roc_auc_score(y, np.nan_to_num(score, nan=0.0))}


def best_f1_threshold(y: np.ndarray, score: np.ndarray) -> float:
    precision, recall, thresholds = precision_recall_curve(y, np.nan_to_num(score, nan=0.0))
    f1 = 2 * precision * recall / np.clip(precision + recall, 1e-12, None)
    return float(thresholds[np.argmax(f1[:-1])])


def precision_recall_f1(positive: np.ndarray, alarm: np.ndarray) -> dict:
    precision, recall, f1, _ = precision_recall_fscore_support(
        positive, alarm, average="binary", zero_division=0
    )
    return {"precision": precision, "recall": recall, "f1": f1}


def at_threshold(
    y: np.ndarray, alarm: np.ndarray, building: np.ndarray, hour: np.ndarray | None = None
) -> dict:
    """Hourly precision/recall/F1 plus event-level recall and alarm volume."""
    label_run = runs(y == 1, building, hour=hour)
    alarm_run = runs(alarm, building, max_gap=1, hour=hour)
    n_label, n_alarm = label_run.max() + 1, alarm_run.max() + 1
    detected = np.unique(label_run[alarm & (label_run >= 0)])
    true_alarm = np.unique(alarm_run[alarm & (y == 1)])
    building_weeks = len(y) / HOURS_PER_WEEK
    return {
        **precision_recall_f1(y == 1, alarm),
        "event_recall": len(detected) / max(n_label, 1),
        "event_precision": len(true_alarm) / max(n_alarm, 1),
        "alarms_per_building_week": n_alarm / building_weeks,
        "false_alarms_per_building_week": (n_alarm - len(true_alarm)) / building_weeks,
    }


def daily(
    y: np.ndarray, score: np.ndarray, alarm: np.ndarray, building: np.ndarray, day: np.ndarray
) -> dict:
    """Building-day metrics as in the LEAD benchmark: a day counts if any of its hours does."""
    frame = pd.DataFrame({"y": y, "score": np.nan_to_num(score, nan=0.0), "alarm": alarm})
    days = frame.groupby([building, day]).max()
    y_day = days["y"].to_numpy() == 1
    counts = precision_recall_f1(y_day, days["alarm"].to_numpy())
    return {
        "day_pr_auc": pr_auc(y_day, days["score"].to_numpy()),
        **{f"day_{k}": v for k, v in counts.items()},
    }


def threshold_for_budget(
    score: np.ndarray, building: np.ndarray, budget: float, hour: np.ndarray | None = None
) -> float:
    """Lowest threshold whose alarm events per building-week stay within budget."""
    score = np.nan_to_num(score, nan=0.0)
    weeks = len(score) / HOURS_PER_WEEK
    lo, hi = np.quantile(score, 0.5), score.max()
    for _ in range(40):
        mid = (lo + hi) / 2
        n_alarm = runs(score >= mid, building, max_gap=1, hour=hour).max() + 1
        lo, hi = (mid, hi) if n_alarm / weeks > budget else (lo, mid)
    return float(hi)


def bootstrap_pr_auc(
    y: np.ndarray, score: np.ndarray, building: np.ndarray, n: int = 200, seed: int = 42
) -> tuple[float, float]:
    """95% interval of PR-AUC when resampling whole buildings; one sort for all draws."""
    rng = np.random.default_rng(seed)
    score = np.nan_to_num(score, nan=0.0)
    order = np.argsort(-score, kind="stable")
    positive = y[order] == 1
    ids, codes = np.unique(building, return_inverse=True)
    codes = codes[order]
    cut = np.r_[np.diff(score[order]) != 0, True]  # last row of each distinct score
    values = []
    for _ in range(n):
        # A building drawn k times weighs k, which equals stacking k copies of its rows.
        weight = np.bincount(rng.choice(len(ids), len(ids)), minlength=len(ids))[codes]
        tp = np.cumsum(weight * positive)[cut]
        if tp[-1] > 0:
            precision = tp / np.maximum(np.cumsum(weight)[cut], 1)
            values.append(np.sum(np.diff(tp, prepend=0) * precision) / tp[-1])
    return float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))
