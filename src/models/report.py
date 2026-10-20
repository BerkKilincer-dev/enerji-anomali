"""Write the committed ML results: markdown summary, JSON, CSV tables and figures."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import lightgbm as lgb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import precision_recall_curve

from src.common.report import md_table, write_json

if TYPE_CHECKING:
    from src.models.train import Context, Evaluation, Result, Trained

COMPARISON_COLUMNS = [
    "model",
    "family",
    "val_pr_auc",
    "pr_auc",
    "pr_auc_ci_low",
    "pr_auc_ci_high",
    "roc_auc",
    "precision",
    "recall",
    "f1",
    "event_recall",
    "event_precision",
    "false_alarms_per_building_week",
]
DAY_COLUMNS = ["model", "day_pr_auc", "day_precision", "day_recall", "day_f1"]
EXAMPLE_DAYS = 14


def feature_importance(booster: lgb.Booster) -> pd.DataFrame:
    gain = booster.feature_importance(importance_type="gain")
    frame = pd.DataFrame({"feature": booster.feature_name(), "gain": gain})
    frame["share"] = frame["gain"] / frame["gain"].sum()
    return frame.sort_values("gain", ascending=False, ignore_index=True)


def _plot_pr_curves(ctx: Context, results: list[Result], path: Path) -> None:
    te = ctx.labeled_rows("test")
    fig, ax = plt.subplots(figsize=(7, 5))
    for r in results:
        score = np.nan_to_num(r.score[te], nan=np.nanmin(r.score[te]))
        precision, recall, _ = precision_recall_curve(ctx.y[te], score)
        ax.plot(recall, precision, label=r.name, linewidth=1.2)
    ax.axhline(ctx.y[te].mean(), color="grey", linestyle=":", label="base rate")
    ax.set(xlabel="recall", ylabel="precision", title="Precision-recall on test buildings")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _plot_importance(importance: pd.DataFrame, path: Path, top: int = 20) -> None:
    head = importance.head(top).iloc[::-1]
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.barh(head["feature"], head["share"])
    ax.set(xlabel="share of total gain", title=f"Top {top} detector features")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _example_window(scored: pd.DataFrame, labels: np.ndarray) -> pd.DataFrame:
    """Test building and two-week window with the most labeled anomaly hours."""
    s = scored.assign(anomaly=labels)
    hours = EXAMPLE_DAYS * 24
    best, best_hits = None, -1
    for _, g in s.groupby("building_id"):
        hits = g["anomaly"].rolling(hours).sum().to_numpy()
        end = int(np.nanargmax(hits)) if np.isfinite(hits).any() else len(g) - 1
        if np.nan_to_num(hits[end]) > best_hits:
            best, best_hits = g.iloc[max(end - hours + 1, 0) : end + 1], np.nan_to_num(hits[end])
    return best


def _plot_example(scored: pd.DataFrame, labels: np.ndarray, path: Path) -> None:
    w = _example_window(scored, labels)
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(w["ts"], w["kwh"], label="actual kWh", linewidth=1)
    ax.plot(w["ts"], w["expected_kwh"], label="expected kWh", linewidth=1, alpha=0.8)
    top = np.nanmax(w[["kwh", "expected_kwh"]].to_numpy()) * 1.05
    ax.fill_between(
        w["ts"],
        0,
        top,
        where=w["anomaly"].to_numpy() == 1,
        color="red",
        alpha=0.15,
        step="mid",
        label="labeled anomaly",
    )
    alarms = w[w["alarm"]]
    ax.scatter(alarms["ts"], alarms["kwh"], s=10, color="black", zorder=3, label="alarm")
    ax.set(title=f"Building {w['building_id'].iat[0]}: actual vs expected", ylabel="kWh")
    ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _plot_scenarios(scenarios: pd.DataFrame, path: Path) -> None:
    pivot = scenarios.pivot(index="scenario", columns="detector", values="detection_rate")
    ax = pivot.plot.bar(figsize=(8, 4), rot=0)
    ax.set(ylabel="detection rate", ylim=(0, 1.05), title="Synthetic scenarios on test buildings")
    ax.legend(fontsize=8)
    ax.figure.tight_layout()
    ax.figure.savefig(path, dpi=120)
    plt.close(ax.figure)


def _markdown(
    summary: dict,
    table: pd.DataFrame,
    budgets: pd.DataFrame,
    scenarios: pd.DataFrame,
    importance: pd.DataFrame,
    events: pd.DataFrame,
) -> str:
    combined = pd.DataFrame([summary["combined_on_test"]["combined"]])
    sample = events.nlargest(10, "est_cost_tl")[
        ["building_id", "ts_start", "source", "explanation"]
    ]
    shares = {k: v for k, v in summary["combined_on_test"].items() if k.startswith("source_share")}
    return "\n".join(
        [
            "# ML results",
            "",
            f"Version `{summary['version']}`, chosen detector `{summary['chosen_detector']}`"
            f"{' (quick run)' if summary['quick'] else ''}. Every number below is measured on the "
            f"{summary['buildings']['test']} held-out test buildings; thresholds were picked on "
            f"the {summary['buildings']['val']} validation buildings.",
            "",
            "## Model comparison",
            "",
            md_table(table[COMPARISON_COLUMNS]),
            "",
            "![PR curves](figures/pr_curves.png)",
            "",
            "## Day level",
            "",
            "A building-day is anomalous if any labeled hour is and alarmed if any hour alarms, "
            "as in the LEAD paper. See [lead-benchmark.md](../../docs/rapor/lead-benchmark.md) "
            "before comparing with the paper.",
            "",
            md_table(table[DAY_COLUMNS]),
            "",
            "## Expected-consumption model (normal test hours)",
            "",
            md_table(pd.DataFrame([summary["expected_model"]])),
            "",
            "## Combined product alarm (rule OR detector OR residual)",
            "",
            md_table(combined),
            "",
            "Alarm source shares: "
            + ", ".join(f"{k[13:]} {v:.0%}" for k, v in shares.items())
            + ".",
            "",
            "## Alarm budget",
            "",
            "Threshold chosen on validation for a target alarm rate, then applied to test.",
            "",
            md_table(budgets),
            "",
            "## Synthetic scenarios",
            "",
            "Waste and meter faults injected into test buildings (LEAD labels are mostly drops).",
            "",
            md_table(scenarios),
            "",
            "![scenarios](figures/scenarios.png)",
            "",
            "## Top features",
            "",
            md_table(importance.head(15)),
            "",
            "![importance](figures/feature_importance.png)",
            "",
            "## Example building",
            "",
            "![example](figures/example_building.png)",
            "",
            f"## Costliest test events ({summary['test_events']['count']} events, "
            f"{summary['test_events']['excess_kwh']:,.0f} kWh above expected)",
            "",
            md_table(sample),
            "",
        ]
    )


def write_reports(
    out: Path, summary: dict, ctx: Context, trained: Trained, ev: Evaluation, booster: lgb.Booster
) -> Path:
    figures = out / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    table, budgets, scenarios, events = trained.table, ev.budgets, ev.scenarios, ev.events
    importance = feature_importance(booster)

    table.to_csv(out / "model_comparison.csv", index=False)
    budgets.to_csv(out / "threshold_table.csv", index=False)
    scenarios.to_csv(out / "scenarios.csv", index=False)
    importance.to_csv(out / "feature_importance.csv", index=False)
    events.nlargest(200, "est_cost_tl").to_csv(out / "test_events_sample.csv", index=False)
    write_json(
        out / "metrics.json",
        {
            **summary,
            "models": table.to_dict(orient="records"),
            "scenarios": scenarios.to_dict(orient="records"),
        },
    )

    labels = ctx.frame.loc[ctx.split == "test", "anomaly"].to_numpy()
    _plot_pr_curves(ctx, trained.results, figures / "pr_curves.png")
    _plot_importance(importance, figures / "feature_importance.png")
    _plot_example(ev.test_scored, labels, figures / "example_building.png")
    _plot_scenarios(scenarios, figures / "scenarios.png")
    (out / "results.md").write_text(
        _markdown(summary, table, budgets, scenarios, importance, events)
    )
    return out
