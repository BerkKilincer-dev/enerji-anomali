"""Single inference path: features -> expected kWh -> rules -> detector -> alarms.

Training, evaluation, the scoring service and the demo all go through
AnomalyScorer.score so they can never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.features.build_features import (
    DETECTOR_FEATURES,
    EXPECTED_FEATURES,
    HISTORY_HOURS,
    build_features,
)
from src.models import rules
from src.models.unsupervised import SCALE_WINDOW, residual_score

HYBRID_EXTRA = ["expected_log", "residual", "residual_score"]
HYBRID_FEATURES = [*DETECTOR_FEATURES, *HYBRID_EXTRA]
SOURCES = ("rule", "ml_sup", "ml_unsup")
# A new building needs a week of history before rules fire and two weeks before models do.
RULE_WARMUP_HOURS = 7 * 24
ML_WARMUP_HOURS = 14 * 24
# Live callers keep this much history before new hours so they score like a full rescore.
CONTEXT_HOURS = HISTORY_HOURS + SCALE_WINDOW
# A waste hit needs the next hour to confirm it, so the last scored hours can still change.
REVISION_HOURS = rules.MIN_WASTE_HOURS - 1


def add_expected(features: pd.DataFrame, frame: pd.DataFrame, expected_log: np.ndarray) -> None:
    """Add expected consumption, its residual and the residual score to `features` in place."""
    features["expected_log"] = np.asarray(expected_log, dtype="float32")
    features["residual"] = (features["log_kwh"] - features["expected_log"]).astype("float32")
    residual = features["residual"].to_numpy()
    features["residual_score"] = residual_score(frame, residual).astype("float32")


@dataclass(frozen=True)
class Thresholds:
    detector: float
    residual: float
    rule: float = rules.FIRE_AT


@dataclass
class AnomalyScorer:
    expected: lgb.Booster
    detector: lgb.Booster
    thresholds: Thresholds
    version: str

    def featurize(self, frame: pd.DataFrame) -> pd.DataFrame:
        features = build_features(frame)
        add_expected(features, frame, self.expected.predict(features[EXPECTED_FEATURES]))
        return features

    def score(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Hourly scores, alarm flag and alarm source for every row of `frame`.

        History is counted inside `frame`, so live callers pass some context before new hours.
        """
        features = self.featurize(frame)
        rule_out = rules.evaluate(features)
        present = frame["kwh"].notna().to_numpy()
        history = pd.Series(present).groupby(frame["building_id"].to_numpy()).cumsum().to_numpy()
        rule_ready, ml_ready = history > RULE_WARMUP_HOURS, history > ML_WARMUP_HOURS

        out = frame[["building_id", "ts", "kwh"]].copy()
        out["expected_kwh"] = np.expm1(features["expected_log"]).clip(lower=0).astype("float32")
        out["residual_score"] = features["residual_score"]
        out["detector_score"] = self.detector.predict(
            features[self.detector.feature_name()]
        ).astype("float32")
        out = pd.concat([out, rule_out], axis=1)

        t = self.thresholds
        rule_hits = present & rule_ready & (out["rule_score"] >= t.rule).to_numpy()
        hits = {
            "rule": rules.persistent(rule_hits, out["rule_type"], out["building_id"].to_numpy()),
            "ml_sup": present & ml_ready & (out["detector_score"] >= t.detector).to_numpy(),
            "ml_unsup": present & ml_ready & (out["residual_score"] >= t.residual).to_numpy(),
        }
        out["source"] = np.select([hits[s] for s in SOURCES], SOURCES, default=None)
        out["alarm"] = out["source"].notna()
        return out
