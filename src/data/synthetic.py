"""Synthetic anomaly scenarios for model testing and the live demo.

Each scenario rewrites the kWh of one building over a window and marks the
affected hours, so detectors can be scored on waste patterns LEAD rarely labels.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.features.calendar import NIGHT_HOURS


@dataclass(frozen=True)
class Scenario:
    name: str
    hours: int
    magnitude: float
    description: str


SCENARIOS = {
    s.name: s
    for s in (
        Scenario("spike", 3, 3.0, "consumption jumps to 3x for a few hours"),
        Scenario("dropout", 24, 0.0, "meter reports zero for a day"),
        Scenario("stuck", 24, 1.0, "meter repeats the same value for a day"),
        Scenario("drift", 336, 1.3, "consumption slowly rises to +30% over two weeks"),
        Scenario("night_base_load", 168, 3.0, "night consumption (00-05) tripled for a week"),
    )
}


SCALABLE = frozenset({"spike", "drift", "night_base_load"})


@dataclass(frozen=True)
class Injection:
    building_id: int
    scenario: str
    start: pd.Timestamp
    magnitude: float | None = None  # overrides the scenario multiplier

    @property
    def spec(self) -> Scenario:
        return SCENARIOS[self.scenario]

    @property
    def factor(self) -> float:
        return self.spec.magnitude if self.magnitude is None else self.magnitude

    @property
    def end(self) -> pd.Timestamp:
        return self.start + pd.Timedelta(hours=self.spec.hours - 1)


def apply(kwh: pd.Series, inj: Injection) -> tuple[pd.Series, pd.Series]:
    """Return modified kWh and a boolean mask of injected hours (index = ts)."""
    out = kwh.astype("float64")
    window = (out.index >= inj.start) & (out.index <= inj.end)
    name, factor = inj.spec.name, inj.factor
    if name == "spike":
        out[window] = out[window] * factor
    elif name == "dropout":
        out[window] = 0.0
    elif name == "stuck":
        out[window] = out[window].iloc[0]
    elif name == "drift":
        out[window] = out[window] * np.linspace(1.0, factor, int(window.sum()))
    elif name == "night_base_load":
        window &= out.index.hour.isin(NIGHT_HOURS)
        out[window] = out[window] * factor
    else:
        raise ValueError(f"unknown scenario {name}")
    return out, pd.Series(window, index=out.index)


def plan(frame: pd.DataFrame, scenario: str, building_ids: list[int], seed: int) -> list[Injection]:
    """Pick one clean window per building: no labels, no gaps, after five weeks of history."""
    rng = np.random.default_rng(seed)
    hours = SCENARIOS[scenario].hours
    injections = []
    for b in building_ids:
        part = frame.loc[frame["building_id"] == b].set_index("ts")
        bad = (part["anomaly"].eq(1) | part["is_missing"]).to_numpy()
        # A window start is valid if no bad hour falls inside [start, start + hours).
        clean = np.convolve(bad, np.ones(hours, dtype=int), mode="valid") == 0
        clean[: 24 * 35] = False
        candidates = np.flatnonzero(clean)
        if len(candidates):
            injections.append(Injection(b, scenario, part.index[rng.choice(candidates)]))
    return injections


def inject(frame: pd.DataFrame, injections: list[Injection]) -> pd.DataFrame:
    """Copy of frame with injected kWh and `injection_id` (-1 outside injected hours)."""
    out = frame.copy()
    out["injection_id"] = -1
    for i, inj in enumerate(injections):
        rows = out.index[out["building_id"] == inj.building_id]
        kwh, mask = apply(out.loc[rows].set_index("ts")["kwh"], inj)
        out.loc[rows, "kwh"] = kwh.to_numpy(dtype="float32")
        hit = rows[mask.to_numpy()]
        out.loc[hit, "injection_id"] = i
    return out
