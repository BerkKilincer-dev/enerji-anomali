# Decisions

Short records of choices that shape the code. Add a new entry instead of editing an old one;
mark replaced entries as superseded.

## D1. LEAD from GitHub, BDG2 for metadata and weather

LEAD 1.0 is the labeled dataset of the Kaggle energy anomaly competition. The GitHub copy needs
no Kaggle account, so anyone can run the pipeline in Colab. BDG2 adds floor area, primary use,
timezone, coordinates and hourly weather for the same buildings. Every file is pinned with a
SHA-256 checksum.

## D2. Postgres, not MongoDB

The data is hourly rows with fixed columns and many joins (readings, buildings, weather,
predictions, feedback). Postgres gives this with roles and SQL tools the team already knows;
TimescaleDB is used when present for compression and fast time ranges, plain tables otherwise.

## D3. Three database roles

Owner for schema changes, `team` for services and imports, `reader` for analysis. Passwords
come from `.env` and must be 12 characters or more. One shared superuser password for everyone
was rejected.

## D4. Building-level split

Train, validation and test are sets of buildings (140/30/30, stratified by anomaly rate,
seed 42), committed in `data/splits.json`. A row-level split would leak a building's own
pattern into the test set. New buildings get a stable split from a hash of their
`external_id`.

## D5. One scorer for every path

`AnomalyScorer.score` is used by training evaluation, the scoring service and the demo.
Separate code for offline and live scoring would drift apart and make reported numbers
meaningless.

## D6. Past-only features and an expected model without the current value

Every feature at hour t uses t and earlier only, so live scoring sees exactly what training
saw. The expected-consumption model gets no current value and no short lags, so a long
anomaly cannot raise its own baseline.

## D7. Rules first, then the detector, then the residual

Rules are explainable and catch waste that LEAD does not label. The supervised detector
catches meter faults it learned from LEAD. The residual covers new buildings and unlabeled
customers. Thresholds come from validation buildings: best F1 for the detector, one event per
building-week for the residual, 0.5 severity for rules.

## D8. Waste rules need two consecutive hours

Single odd night or weekend hours produced many one-hour false alarms. Requiring two hours
removed them at the cost of reporting a waste event one hour later.

## D9. Warm-up before alarms

Rules start after 7 days of history and models after 14 days, because baselines such as the
same hour in past weeks are empty before that. A new customer sees no alarms in the first
week instead of noisy ones.

## D10. Location per building, Istanbul by default

Weather depends on location, so every building has coordinates and a `weather_key`
(`geo:<lat>,<lng>`, 2 decimals, about 1 km) shared by nearby buildings. Customers without
coordinates fall back to Istanbul. Training uses BDG2 station weather; customers use
Open-Meteo, which needs no API key.

## D11. Naive local time in the database

Readings, weather and features are stored in the building's local time without an offset,
because consumption follows local hours, weekdays and holidays. Imports and MQTT convert
offsets and DST before storing.

## D12. Customer ids from 100000

LEAD keeps its BDG2 building ids; customers start at 100000, so public and customer data never
collide and LEAD can be reloaded at any time. `external_id` is the customer's own unique id.

## D13. Model bundle as LightGBM text and JSON

No pickle: loading a model cannot execute code, and the bundle is readable and diffable. The
bundle is stored in `model_artifacts`, so services need no shared file system; the scoring
service switches to a newer version on its next cycle.

## D14. Batch and live output side by side

`origin = 'batch'` holds the published offline evaluation, `origin = 'live'` the scoring
service. Once the scoring service covers a building (a `scoring_state` row exists), the API
reads only its live output through the `current_predictions` and `current_anomalies` views,
so old batch events do not mix with live ones. A new training run replaces only batch rows.

## D15. Feedback is the long-term label source

Operator verdicts become hourly labels (confirmed 1, false alarm and planned 0, latest wins).
Supervised models train only on labeled hours, so unlabeled customer hours never become
negatives.

## D16. CPU training in Colab

The data fits in memory and LightGBM trains on CPU in minutes; a GPU or TPU would not speed it
up meaningfully. A `--quick` mode with 20% of buildings exists for smoke tests.

## D17. API key for writes, demo data without a database

One key in `X-API-Key` protects writes during development; reads are open. Without
`DATABASE_URL` the API serves fixed demo data so frontend work never waits for data. User
accounts replace the key before customers use the system.

## D18. Cost from a tariff per building

Excess kWh above the expected model times the building's tariff (`TARIFF_TL_PER_KWH`,
default 3.5 TL/kWh, a placeholder). The cost is an estimate of avoidable spending, not a
billing figure.

## D19. Live scoring matches a full rescore

Each cycle scores the new hours with enough history for every feature and the residual scale
(`CONTEXT_HOURS`) and rewrites the last scored hour (`REVISION_HOURS`), because a waste alarm
on that hour can only be confirmed by the next one. An hour scored live therefore gets the
same alarm as in a rescore of the whole building. An event that may still be running is
rescored from its own start, so a long event stays one row. The cost is reading about six weeks
of history per building and cycle, more while a long event is open.

## D20. Partial and merged hours count as missing

An hour built from fewer or more meter intervals than expected does not hold a true hourly
total. The modeling frame treats such hours as missing, so they neither raise alarms nor
shift baselines; the raw value stays in `readings` with its `quality` flag.

## D21. Modular monolith shaped like the target services

The target architecture has separate identity, asset, ingestion, telemetry, anomaly, alert,
reporting and work order services. A team of four does not need ten deployments yet, so the
code stays one repository with one Postgres and three processes, but every service owns its
own tables (see [backend.md](backend.md#service-map)). Splitting a service later moves code,
not data. MQTT stays the only broker until two services need to exchange events.

## D22. Alerts once per event and channel, recent events only

A `notifications` row per event and channel makes sending idempotent across restarts and
keeps an audit trail; a flag on `anomalies` could not support a second channel. Only events
that ended in the last 24 hours of the building's local time by the wall clock are sent, so a
replayed backlog or a scoring outage does not flood the chat. The dashboard's active alerts
count from each building's latest reading instead, so a building that stopped reporting still
shows its last events there.

## D23. Day-level metrics next to the hourly ones

The LEAD paper scores 24-hour windows: a day is anomalous if any of its hours is. Training
reports the same day-level PR-AUC, precision, recall and F1 beside the hourly metrics, so the
models can be placed next to the published numbers. The paper splits by sample and puts most
anomalous days in its test set, so the comparison is a reference, not a leaderboard
([rapor/lead-benchmark.md](rapor/lead-benchmark.md)). Thresholds stay hourly.

## D24. MLflow with a file store by default

Every training run logs params, metrics, reports and the bundle to MLflow. Without
`MLFLOW_TRACKING_URI` the store is SQLite plus artifacts in `MLFLOW_DIR`, which notebook 02
puts on Google Drive, so Colab runs survive without a tracking server. A shared server is one
environment variable away. The bundle in `models_out` and `model_artifacts` stays the
deployable artifact; MLflow is the run history.

## D25. Template explanations, no LLM yet

Each event carries a short explanation from a template per rule or detector, with the excess
kWh and TL cost. An LLM-written Turkish suggestion was planned but is left out for now: it
adds a paid external call per event, sends building data to a third party (KVKK), and can
invent causes the data does not show. The templates cover what the system actually knows; an
LLM can be added later on top of the same event fields.

## D26. Event metrics follow the clock, publishing is opt-in

Supervised metrics run on labeled hours only, so dropping unlabeled hours made distant alarms
look adjacent and merged them into one event. Event counts now use each row's hour, which
matters most for customer data where only feedback hours are labeled. Publishing from notebook
02 needs `PUBLISH = True`, because the scoring service switches to every newly published model.
