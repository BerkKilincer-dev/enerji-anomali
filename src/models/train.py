"""Train, compare and evaluate every detector on the same building split.

Usage: python -m src.models.train [--source files|db] [--tune N] [--no-mlflow] [--publish] [--quick]
Writes reports/ml/* (committed results) and the scorer bundle to MODEL_DIR.
With --source db every building in the database is used and operator feedback
becomes labels; supervised models only ever see labeled hours.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import cached_property
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.config import ROOT, SEED, get_settings
from src.data import synthetic
from src.data.dataset import ensure_tables, model_frame
from src.data.prepare import Tables
from src.features.build_features import (
    DETECTOR_FEATURES,
    EXPECTED_FEATURES,
    NO_WEATHER_FEATURES,
    build_features,
)
from src.models import boosting, metrics, registry, rules, supervised, tracking, unsupervised
from src.models.detector import (
    HYBRID_FEATURES,
    SOURCES,
    AnomalyScorer,
    Thresholds,
    add_expected,
)
from src.models.events import build_events
from src.models.report import write_reports

log = logging.getLogger(__name__)
BUDGETS = (0.5, 1, 2, 3, 5, 10)


@dataclass
class Context:
    frame: pd.DataFrame
    features: pd.DataFrame
    y: np.ndarray
    building: np.ndarray
    split: np.ndarray
    present: np.ndarray
    source: str = "files"
    timings: dict = field(default_factory=dict)

    @property
    def labeled(self) -> np.ndarray:
        return self.present & ~np.isnan(self.y)

    @cached_property
    def hour(self) -> np.ndarray:
        return metrics.hour_index(self.frame["ts"])

    def rows(self, name: str) -> np.ndarray:
        """Hours with a reading; used by the expected and unsupervised models."""
        return self.present & (self.split == name)

    def labeled_rows(self, name: str) -> np.ndarray:
        """Hours with a reading and a label; used to train and evaluate supervised models."""
        return self.labeled & (self.split == name)

    def x(self, name: str, columns: list[str]) -> pd.DataFrame:
        return self.features.loc[self.labeled_rows(name), columns]

    @contextmanager
    def timed(self, key: str) -> Iterator[None]:
        start = time.time()
        yield
        self.timings[key] = round(time.time() - start, 1)
        log.info("%s done in %.1fs", key, self.timings[key])


@dataclass
class Result:
    name: str
    family: str
    score: np.ndarray  # full length, NaN where not scored


def load_tables(source: str) -> Tables:
    if source == "files":
        return ensure_tables()
    from src.common.db import connect
    from src.data.feedback import apply_feedback, fetch_feedback
    from src.data.store import fetch_tables

    url = get_settings().database_url
    if not url:
        raise ValueError("--source db needs DATABASE_URL")
    with connect(url) as conn:
        tables = fetch_tables(conn)
        feedback = fetch_feedback(conn)
    log.info(
        "db: %d buildings, %d readings, %d feedback verdicts",
        len(tables.buildings),
        len(tables.readings),
        len(feedback),
    )
    return replace(tables, readings=apply_feedback(tables.readings, feedback))


def build_context(source: str = "files", quick: bool = False) -> Context:
    frame = model_frame(load_tables(source))
    if quick:
        keep = (
            frame.groupby("split")["building_id"].unique().map(lambda b: b[: max(6, len(b) // 5)])
        )
        frame = frame[frame["building_id"].isin(np.concatenate(keep.to_numpy()))].reset_index(
            drop=True
        )
    return Context(
        frame=frame,
        features=build_features(frame),
        y=frame["anomaly"].to_numpy(dtype="float32"),
        building=frame["building_id"].to_numpy(),
        split=frame["split"].to_numpy(dtype=str),
        present=frame["kwh"].notna().to_numpy(),
        source=source,
    )


def fit_expected(ctx: Context):
    """Expected-consumption model; training rows get out-of-fold predictions."""
    with ctx.timed("expected_model"):
        target = ctx.features["log_kwh"].to_numpy()
        tr, va = ctx.rows("train"), ctx.rows("val")
        x_tr = ctx.features.loc[tr, EXPECTED_FEATURES]
        x_va = ctx.features.loc[va, EXPECTED_FEATURES]
        model = unsupervised.train_expected(x_tr, target[tr], x_va, target[va])
        expected_log = np.empty(len(target))
        expected_log[tr] = unsupervised.out_of_fold_expected(
            x_tr, target[tr], ctx.building[tr], x_va, target[va]
        )
        x_rest = ctx.features.loc[~tr, EXPECTED_FEATURES]
        expected_log[~tr] = model.predict(x_rest, num_iteration=model.best_iteration)
        add_expected(ctx.features, ctx.frame, expected_log)
        return model


def regression_quality(ctx: Context) -> dict:
    rows = ctx.rows("test") & (ctx.y != 1)
    actual = np.expm1(ctx.features.loc[rows, "log_kwh"])
    pred = np.expm1(ctx.features.loc[rows, "expected_log"])
    ape = np.abs(actual - pred) / actual.clip(lower=1)
    return {
        "mae_kwh": float(np.mean(np.abs(actual - pred))),
        "median_ape": float(np.median(ape)),
        "wape": float(np.abs(actual - pred).sum() / actual.sum()),
    }


def _full(ctx: Context, values: np.ndarray, rows: np.ndarray) -> np.ndarray:
    out = np.full(len(ctx.y), np.nan)
    out[rows] = values
    return out


def run_detectors(ctx: Context, tune: int = 0) -> tuple[list[Result], dict]:
    results, boosters = [], {}
    scored = ctx.present & (ctx.split != "train")
    tr, va = ctx.labeled_rows("train"), ctx.labeled_rows("val")

    with ctx.timed("rules"):
        equipment = ctx.frame.get("equipment_type")
        rule_score = rules.evaluate(ctx.features, equipment)["rule_score"].to_numpy()
        results.append(Result("rules", "rules", rule_score))

    with ctx.timed("random_forest"):
        rf = supervised.train_random_forest(ctx.x("train", DETECTOR_FEATURES), ctx.y[tr])
        rf_x = supervised.rf_matrix(ctx.features.loc[scored, DETECTOR_FEATURES])
        rf_score = rf.predict_proba(rf_x)[:, 1]
        results.append(Result("random_forest", "supervised", _full(ctx, rf_score, scored)))

    with ctx.timed("isolation_forest"):
        if_columns = unsupervised.IF_FEATURES
        iso = unsupervised.train_isolation_forest(ctx.features.loc[ctx.rows("train"), if_columns])
        iso_score = unsupervised.isolation_score(iso, ctx.features.loc[scored, if_columns])
        results.append(Result("isolation_forest", "unsupervised", _full(ctx, iso_score, scored)))

    results.append(
        Result("expected_residual", "unsupervised", ctx.features["residual_score"].to_numpy())
    )

    variants = {
        "lgbm": DETECTOR_FEATURES,
        "lgbm_no_weather": NO_WEATHER_FEATURES,
        "lgbm_hybrid": HYBRID_FEATURES,
    }
    for name, columns in variants.items():
        with ctx.timed(name):
            tuned = tune and name == "lgbm_hybrid"
            params = tune_lightgbm(ctx, columns, tune) if tuned else None
            booster = supervised.train_lightgbm(
                ctx.x("train", columns), ctx.y[tr], ctx.x("val", columns), ctx.y[va], params=params
            )
            boosters[name] = booster
            score = booster.predict(
                ctx.features.loc[scored, columns], num_iteration=booster.best_iteration
            )
            results.append(Result(name, "supervised", _full(ctx, score, scored)))
    return results, boosters


def tune_lightgbm(ctx: Context, columns: list[str], trials: int) -> dict:
    """Small Optuna search on validation PR-AUC (optional)."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    tr, va = ctx.labeled_rows("train"), ctx.labeled_rows("val")
    pair = boosting.datasets(
        ctx.x("train", columns), ctx.y[tr], ctx.x("val", columns), ctx.y[va], reusable=True
    )

    def objective(trial: optuna.Trial) -> float:
        params = {
            "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.15, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 15, 255, log=True),
            "min_child_samples": trial.suggest_int("min_child_samples", 20, 1000, log=True),
            "feature_fraction": trial.suggest_float("feature_fraction", 0.5, 1.0),
            "lambda_l2": trial.suggest_float("lambda_l2", 1e-3, 10, log=True),
        }
        booster = boosting.fit_lightgbm({**supervised.LGB_PARAMS, **params}, *pair, 1500)
        return booster.best_score["valid_0"]["average_precision"]

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=SEED))
    study.optimize(objective, n_trials=trials)
    log.info("best tuned val PR-AUC %.4f", study.best_value)
    return study.best_params


def evaluate(ctx: Context, results: list[Result]) -> pd.DataFrame:
    """Threshold picked on validation (best F1), metrics reported on test buildings."""
    va, te = ctx.labeled_rows("val"), ctx.labeled_rows("test")
    y_va, y_te, building, hour = ctx.y[va], ctx.y[te], ctx.building[te], ctx.hour[te]
    day = ctx.frame["ts"].dt.normalize().to_numpy()[te]
    rows = []
    for r in results:
        val, test = r.score[va], r.score[te]
        threshold = metrics.best_f1_threshold(y_va, val)
        alarm = np.nan_to_num(test, nan=0.0) >= threshold
        low, high = metrics.bootstrap_pr_auc(y_te, test, building)
        rows.append(
            {
                "model": r.name,
                "family": r.family,
                "val_pr_auc": metrics.pr_auc(y_va, val),
                **metrics.ranking(y_te, test),
                "pr_auc_ci_low": low,
                "pr_auc_ci_high": high,
                "threshold": threshold,
                **metrics.at_threshold(y_te, alarm, building, hour),
                **metrics.daily(y_te, test, alarm, building, day),
            }
        )
    return pd.DataFrame(rows).sort_values("pr_auc", ascending=False, ignore_index=True)


def threshold_table(ctx: Context, score: np.ndarray, residual: np.ndarray) -> pd.DataFrame:
    """Alarm budget -> threshold chosen on validation -> realized test behaviour."""
    va, te = ctx.labeled_rows("val"), ctx.labeled_rows("test")
    rows = []
    for budget in BUDGETS:
        threshold = metrics.threshold_for_budget(score[va], ctx.building[va], budget, ctx.hour[va])
        res_threshold = metrics.threshold_for_budget(
            residual[va], ctx.building[va], budget, ctx.hour[va]
        )
        alarm = np.nan_to_num(score[te]) >= threshold
        rows.append(
            {
                "budget_per_building_week": budget,
                "detector_threshold": threshold,
                "residual_threshold": res_threshold,
                **metrics.at_threshold(ctx.y[te], alarm, ctx.building[te], ctx.hour[te]),
            }
        )
    return pd.DataFrame(rows)


def evaluate_combined(ctx: Context, scorer: AnomalyScorer) -> tuple[dict, pd.DataFrame]:
    test_frame = ctx.frame[ctx.split == "test"].reset_index(drop=True)
    scored = scorer.score(test_frame)
    y = test_frame["anomaly"].to_numpy(dtype="float32")
    keep = scored["kwh"].notna().to_numpy() & ~np.isnan(y)
    b, hour = scored["building_id"].to_numpy()[keep], metrics.hour_index(scored["ts"])[keep]
    out = {"combined": metrics.at_threshold(y[keep], scored["alarm"].to_numpy()[keep], b, hour)}
    for source in SOURCES:
        hit = scored["source"].to_numpy() == source
        out[f"source_share_{source}"] = float(hit.sum() / max(scored["alarm"].sum(), 1))
    return out, scored


def evaluate_scenarios(ctx: Context, scorer: AnomalyScorer) -> pd.DataFrame:
    """Inject each synthetic scenario into every test building and check detection."""
    base = ctx.frame[ctx.split == "test"].reset_index(drop=True)
    test_ids = sorted(base["building_id"].unique().tolist())
    detectors = {
        "rules": lambda s: s["rule_score"] >= scorer.thresholds.rule,
        "ml_sup": lambda s: s["detector_score"] >= scorer.thresholds.detector,
        "ml_unsup": lambda s: s["residual_score"] >= scorer.thresholds.residual,
        "combined": lambda s: s["alarm"],
    }
    rows = []
    for i, name in enumerate(synthetic.SCENARIOS):
        injections = synthetic.plan(base, name, test_ids, seed=SEED + i)
        frame = synthetic.inject(base, injections)
        scored = scorer.score(frame)
        inj_id = frame["injection_id"].to_numpy()
        for det, rule in detectors.items():
            alarm = rule(scored).to_numpy() & frame["kwh"].notna().to_numpy()
            hit_rows = (inj_id >= 0) & alarm
            detected = np.unique(inj_id[hit_rows])
            delays = []
            for k in detected:
                first = scored.loc[(inj_id == k) & alarm, "ts"].min()
                delays.append((first - injections[k].start) / pd.Timedelta(hours=1))
            rows.append(
                {
                    "scenario": name,
                    "detector": det,
                    "injections": len(injections),
                    "detection_rate": len(detected) / max(len(injections), 1),
                    "hourly_recall": hit_rows.sum() / max((inj_id >= 0).sum(), 1),
                    "median_hours_to_detect": float(np.median(delays)) if delays else np.nan,
                }
            )
    return pd.DataFrame(rows)


def _version() -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = "nogit"
    return f"{datetime.now():%Y%m%d-%H%M}-{sha}"


@dataclass
class Trained:
    expected: lgb.Booster
    regression: dict
    results: list[Result]
    boosters: dict[str, lgb.Booster]
    table: pd.DataFrame

    def result(self, name: str) -> Result:
        return next(r for r in self.results if r.name == name)


@dataclass
class Evaluation:
    combined: dict
    test_scored: pd.DataFrame
    scenarios: pd.DataFrame
    budgets: pd.DataFrame
    events: pd.DataFrame


def train_models(ctx: Context, tune: int = 0) -> Trained:
    if any(np.unique(ctx.y[ctx.labeled_rows(name)]).size < 2 for name in ("val", "test")):
        raise ValueError("validation and test buildings need labeled normal and anomalous hours")
    expected = fit_expected(ctx)
    regression = regression_quality(ctx)
    results, boosters = run_detectors(ctx, tune)
    return Trained(expected, regression, results, boosters, evaluate(ctx, results))


def chosen_detector(trained: Trained) -> str:
    """LightGBM variant with the best validation PR-AUC, so test numbers stay unbiased."""
    boosters = trained.table[trained.table["model"].isin(trained.boosters)]
    return boosters.loc[boosters["val_pr_auc"].idxmax(), "model"]


def build_scorer(ctx: Context, trained: Trained) -> AnomalyScorer:
    """Detector threshold from the comparison table; residual threshold for one alarm a week."""
    chosen = chosen_detector(trained)
    va = ctx.rows("val")
    thresholds = Thresholds(
        detector=float(trained.table.set_index("model").loc[chosen, "threshold"]),
        residual=metrics.threshold_for_budget(
            trained.result("expected_residual").score[va], ctx.building[va], 1.0, ctx.hour[va]
        ),
    )
    return AnomalyScorer(trained.expected, trained.boosters[chosen], thresholds, _version())


def evaluate_product(
    ctx: Context, trained: Trained, scorer: AnomalyScorer, tariff: float
) -> Evaluation:
    combined, test_scored = evaluate_combined(ctx, scorer)
    budgets = threshold_table(
        ctx,
        trained.result(chosen_detector(trained)).score,
        trained.result("expected_residual").score,
    )
    return Evaluation(
        combined,
        test_scored,
        evaluate_scenarios(ctx, scorer),
        budgets,
        build_events(test_scored, tariff),
    )


def summarize(
    ctx: Context,
    trained: Trained,
    scorer: AnomalyScorer,
    ev: Evaluation,
    tariff: float,
    quick: bool,
) -> dict:
    return {
        "version": scorer.version,
        "chosen_detector": chosen_detector(trained),
        "quick": quick,
        "source": ctx.source,
        "buildings": {
            name: int(count)
            for name, count in pd.Series(ctx.building).groupby(ctx.split).nunique().items()
        },
        "rows": len(ctx.y),
        "labeled_rows": int(ctx.labeled.sum()),
        "tariff_tl_per_kwh": tariff,
        "thresholds": vars(scorer.thresholds),
        "expected_model": trained.regression,
        "combined_on_test": ev.combined,
        "timings_s": ctx.timings,
        "test_events": {
            "count": len(ev.events),
            "waste": int((ev.events["category"] == "waste").sum()),
            "data_issue": int((ev.events["category"] == "data_issue").sum()),
            "excess_kwh": float(ev.events["excess_kwh"].sum()),
            "est_cost_tl": float(ev.events["est_cost_tl"].sum()),
        },
    }


def save_run(
    ctx: Context,
    trained: Trained,
    scorer: AnomalyScorer,
    ev: Evaluation,
    tracker: tracking.Tracker,
    quick: bool = False,
    tune: int = 0,
) -> tuple[dict, Path, Path]:
    """Save the bundle and reports and log them to MLflow; shared by the CLI and notebook 02."""
    settings = get_settings()
    summary = summarize(ctx, trained, scorer, ev, settings.tariff_tl_per_kwh, quick)
    model_dir = registry.save(scorer, settings.model_dir, metrics=summary)
    out = write_reports(settings.reports_dir / "ml", summary, ctx, trained, ev, scorer.detector)
    keys = ("version", "chosen_detector", "source", "quick")
    tracker.params({**{k: summary[k] for k in keys}, "tune_trials": tune, **summary["thresholds"]})
    for record in trained.table.to_dict("records"):
        tracker.metrics(record, prefix=f"{record['model']}.")
    tracker.metrics(trained.regression, prefix="expected.")
    tracker.metrics(ev.combined["combined"], prefix="combined.")
    tracker.artifacts(out, "reports")
    tracker.artifacts(model_dir, "model")
    return summary, model_dir, out


def run(
    source: str = "files",
    tune: int = 0,
    use_mlflow: bool = True,
    publish: bool = False,
    quick: bool = False,
) -> dict:
    settings = get_settings()
    with tracking.run("train", enabled=use_mlflow) as tracker:
        ctx = build_context(source, quick)
        trained = train_models(ctx, tune)
        scorer = build_scorer(ctx, trained)
        ev = evaluate_product(ctx, trained, scorer, settings.tariff_tl_per_kwh)
        summary, model_dir, _ = save_run(ctx, trained, scorer, ev, tracker, quick, tune)

    if publish and settings.database_url:
        from src.models.publish import publish_results

        publish_results(settings.database_url, model_dir, ev.test_scored, ev.events)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["files", "db"], default="files")
    parser.add_argument("--tune", type=int, default=0, help="Optuna trials for lgbm_hybrid")
    parser.add_argument("--no-mlflow", action="store_true")
    parser.add_argument(
        "--publish", action="store_true", help="write model + results to DATABASE_URL"
    )
    parser.add_argument("--quick", action="store_true", help="20%% of buildings, for smoke tests")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run(args.source, args.tune, not args.no_mlflow, args.publish, args.quick)
    print(f"chosen detector: {summary['chosen_detector']}, version {summary['version']}")


if __name__ == "__main__":
    main()
