# Handover

State of the data and ML work, and how to pick it up. Update this page at the end of every
larger change.

## State

| Area | State |
|---|---|
| Data pipeline | complete; full run in notebook 01 or `python -m src.data.prepare` |
| Quality report | `reports/data/` from the full dataset |
| ML pipeline | complete; `reports/ml/` is a **quick run** (20% of buildings) |
| Shared database | schema, roles and loader ready; no shared instance yet |
| Live path | registration, CSV import, MQTT replay with an injected anomaly, ingest, scoring, alert dispatch and dashboard work end to end locally (Telegram itself not yet tried with a real bot) |
| Tracking | MLflow on every training run; SQLite store, on Drive from notebook 02 |
| Tests | unit tests run everywhere; database tests need `TEST_DATABASE_URL` |

## First steps for the next person

1. Read [architecture.md](architecture.md), then [data.md](data.md) or
   [machine-learning.md](machine-learning.md).
2. Data engineer: run notebook 01 in Colab (set the `GITHUB_TOKEN` secret while the repository
   is private), then load the shared database with the owner URL in the `DATABASE_URL` secret.
3. ML developer: run notebook 02 with `QUICK = False`. Commit the new `reports/ml/` and keep
   `models_out/` out of git; publish the model with `--publish` when the database is ready.
4. Everyone else: `docker compose up -d --build`, then work against the demo or local data.

## Verifying a change

```bash
ruff check . && ruff format --check .
pytest -q
TEST_DATABASE_URL=postgresql://owner:<password>@localhost:5432/energy_test pytest -q
cd frontend && npm run build
```

`energy_test` must be a separate database used only for tests; every test creates and drops
its own schema in it.

## Things to know

- The committed ML numbers come from a quick run and only prove the pipeline works.
- LEAD labels are mostly meter faults; waste alarms count as false against them, so the
  combined alarm looks weaker than it is. Synthetic scenarios and feedback are the fair
  measures for waste.
- Slow drift is mostly missed; it is the first ML item on the [roadmap](roadmap.md).
- The default tariff (3.5 TL/kWh) is a placeholder; set the real tariff per building.
- Live scoring needs 14 days of history per building before model alarms appear.
- Late readings for hours that were already scored are stored but not rescored; see the known
  limits in [backend.md](backend.md).
- Re-running `load_db` is safe; `--force` reloads LEAD data.
- Alerts go only for events that ended in the last 24 hours, so the first scoring cycle after
  a long backlog sends at most a few messages; set `TELEGRAM_*` to turn them on.

## Where things are decided

[decisions.md](decisions.md) explains the choices behind the split, the scorer, thresholds,
locations and storage. Change a decision by adding a new entry.
