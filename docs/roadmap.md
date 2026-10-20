# Roadmap

Where the project stands and what each role picks up next. Items are ordered by value; open
one issue per item.

## Done

- Data pipeline: download with checksums, LEAD + BDG2 preparation, kBTU conversion, building
  split, quality report, idempotent database load with team and reader roles.
- Customer data path: building registration with location, CSV import with unit, timezone,
  DST and cumulative-register handling, MQTT ingest, Open-Meteo weather, feedback as labels.
- ML pipeline: past-only features with weather and holidays, expected-consumption model,
  rules, LightGBM, Random Forest, Isolation Forest, residual detector, alarm budget table,
  synthetic scenarios, day-level metrics next to the LEAD paper, model registry without
  pickle, MLflow tracking in the CLI and the notebook.
- Services: FastAPI with demo mode, live scoring service with Telegram alerts, virtual
  meters for demos with injected anomalies.
- Schema for the target services: tenants, users, assets, meters, alert settings,
  notifications, work orders and a daily KPI view.
- Dashboard: overview, building chart against expected load, anomaly table with feedback,
  phone layout.
- Colab notebooks for data and ML, tests with database integration, Docker Compose stack.

## Data engineer

1. Run notebook 01 once on the full dataset and load the shared database.
2. A shared Postgres for the team outside anyone's laptop (managed free tier), with the
   `team` and `reader` roles.
3. Retry failed Open-Meteo calls with a scheduled `backfill_weather` job.
4. Data quality checks on live data: missing hours per building per day, stuck meters,
   sudden unit changes; store them in a table the dashboard can show.
5. Retention and compression for `readings` (TimescaleDB compression after 90 days).
6. School calendars and local holidays as data, not code, so new countries are a data change.

## ML developer

1. Full training run in Colab (notebook 02, CPU) and commit the new `reports/ml`.
2. Drift detection: weekly CUSUM on the residual, or the same week last year, so slow
   increases are reported (the weakest scenario today).
3. Thresholds per segment and per customer from the alarm budget table, stored with the
   building.
4. Retrain monthly with `--source db --publish` once feedback exists; compare against the
   current model before switching.
5. Probability calibration (isotonic on validation) if scores are shown to users.
6. Check the BDG2 weather against Open-Meteo for the same sites; retrain the expected model on
   Open-Meteo history if the gap is large.

## Backend developer

1. Login, sessions and roles on the `users` table instead of the shared API key (see
   [security.md](security.md)).
2. Tenant filtering: every query limited to the user's `buildings.tenant_id`.
3. Endpoints for assets, meters, `alert_settings` and work orders (tables are ready, see the
   [service map](backend.md#service-map)); closing a work order writes feedback.
4. More alert channels (e-mail, a Telegram chat per tenant) and a daily digest.
5. Endpoints for weekly and monthly cost reports on `daily_kpis`, and for building edits.
6. Rate limits and a reverse proxy with TLS.
7. Health checks for scoring and ingest (last scored hour, last message per building).
8. Rescore hours that receive late readings, and replace forecast weather with archive
   values once Open-Meteo publishes them (see the known limits in [backend.md](backend.md)).

## Frontend developer

1. Building registration form and CSV upload with the import report.
2. Turkish labels and a language switch.
3. Alarm filters (open, confirmed, false alarm), cost trend per week and per building.
4. Feedback with a note; show who gave it.
5. Login page once the backend has accounts.

## Product

1. Interview two or three facility managers about alarms they would act on.
2. A pilot with one real building: meter export or MQTT gateway, two weeks of history, then
   live scoring and weekly feedback.
3. Measure confirmed alarms and estimated savings per building per month.
4. Answer the open questions in [commercial-readiness.md](commercial-readiness.md).
