# Data

Owner: data engineer. Code in `src/data`, entry point `notebooks/01_data_pipeline.ipynb`.

## Sources

| Source | What | Pinned in |
|---|---|---|
| [LEAD 1.0](https://github.com/samy101/lead-dataset) (small) | 200 buildings, hourly meter readings for 2016, hourly anomaly labels | `src/data/sources.py` |
| [BDG2](https://github.com/buds-lab/building-data-genome-project-2) metadata | primary use, floor area, year built, site, timezone, coordinates | `src/data/sources.py` |
| BDG2 weather | hourly air and dew temperature, wind, cloud cover, precipitation, pressure per site | `src/data/sources.py` |
| [Open-Meteo](https://open-meteo.com) | hourly weather for customer locations, no API key | `src/data/weather_api.py` |

LEAD is the dataset of the Kaggle competition
[energy-anomaly-detection](https://www.kaggle.com/competitions/energy-anomaly-detection);
the GitHub copy is used because it needs no Kaggle account. Every download is checked against a
SHA-256 checksum (`python -m src.data.download`).

## Preparation

`python -m src.data.prepare` writes three parquet files to `data/processed`:

| Table | Rows | Notes |
|---|---|---|
| `buildings` | 200 | BDG2 metadata, `country` from timezone, `external_id` = BDG2 name |
| `weather` | 122,976 | BDG2 units, gaps up to 3 hours forward-filled |
| `readings` | 1,749,494 | `kwh`, `meter_raw`, `anomaly` |

- Site 0 (8 buildings) reports kBTU; it is converted with 1 kBTU = 0.2931 kWh.
- `meter_raw` keeps the original value; `kwh` is the converted one.
- `model_frame` (`src/data/dataset.py`) puts every building on a complete hourly grid
  (1,756,800 rows), joins metadata, weather and split, and is what training and scoring use.

Quality report: [reports/data/quality.md](../reports/data/quality.md)
(`python -m src.data.quality`). Highlights: 6.5% missing hours, 2.3% anomaly rate, labeled
anomalies are mostly drops and zero readings rather than spikes, 10.6% of hours repeat the
previous value.

## Split

Building-level, never row-level: 140 train, 30 validation, 30 test, stratified by anomaly
rate (`data/splits.json`, committed, seed 42). Buildings that are not in the file, such as new
customers, get a stable split from a hash of their `external_id`. Precedence: an explicit split
argument, then `buildings.split` in the database, then `splits.json`, then the hash.

## Shared database

Schema: [db/init/001_schema.sql](../db/init/001_schema.sql), idempotent, applied by
`python -m src.data.load_db` and by the `init` container. Tables live in four Postgres
schemas, so the model data is apart from customer setup and operations:

| Schema | Tables and views | Written by |
|---|---|---|
| `core` | `tenants`, `users`, `buildings`, `operating_hours`, `planned_downtime`, `assets`, `meters` | onboarding (CLI, `POST /tenants`, `POST /buildings`) |
| `telemetry` | `readings`, `weather` | CSV import, MQTT ingest, weather backfill, `load_db` |
| `ml` | `predictions`, `anomalies`, `anomaly_feedback`, `scoring_state`, `model_artifacts`; views `readings_enriched`, `current_predictions`, `current_anomalies` | training, scoring, feedback |
| `ops` | `alert_settings`, `notifications`, `work_orders`; view `daily_kpis` | alerts, reporting |

Every connection from `src.common.db.connect` sets `search_path` to
`core,telemetry,ml,ops,public`, and `load_db` sets the same path on the database for psql and
DataGrip, so code and ad hoc SQL use plain table names. `ml.readings_enriched` is the model
input in one place: readings joined with building metadata, segment and weather.

A database created before the split keeps its data: applying the schema moves the old
tables out of `public` with `ALTER TABLE ... SET SCHEMA` (indexes and sequences move with
them) and recreates the views. `load_db` then grants the team and reader roles their rights in
the four schemas again, also when their passwords are not set in that run. Buildings without a
segment get the default from their BDG2 primary use on every schema run. In DataGrip, reconnect and tick the four schemas in the
data source's schema list; new schemas are not shown until they are selected.

| Role | Rights | Used by |
|---|---|---|
| owner (`POSTGRES_USER`) | everything, owns the database | schema changes, `load_db` |
| team (`DB_TEAM_USER`) | read and write data in all four schemas | API, scoring, ingest, imports, training with `--source db` |
| reader (`DB_READER_USER`) | read only in all four schemas | analysis, notebooks, BI tools |

Passwords come from `.env` and need at least 12 characters; they are never stored in the
repository. Connection strings:

```
postgresql://team:<DB_TEAM_PASSWORD>@<host>:5432/energy
postgresql://reader:<DB_READER_PASSWORD>@<host>:5432/energy
```

`load_db` is idempotent: it skips loading when LEAD data is already present and `--force`
reloads it. `--schema-only` creates the schema and roles without data.

## Customer data

Real buildings keep sending data, so every path below is incremental and idempotent.

### Register a tenant

```bash
python -m src.data.customer tenant --name "Acme A.S." --tax-number 1234567890 \
    --tax-office Kadikoy --city Istanbul --contact-email ops@acme.example --status pilot
```

or `POST /tenants` (field rules in [api.md](api.md#post-tenants)). A tenant is one customer
company and is matched by name: registering the same name again updates only the fields that
are given. Two tenants cannot share a tax number.

### Register a building

```bash
python -m src.data.customer register --external-id acme-hq --name "Acme HQ" --tenant-id 1 \
    --lat 41.01 --lng 28.98 --primary-use Office --segment office --square-feet 54000 --tariff 4.2
```

or `POST /buildings`. `--segment` is one of the fixed segments in `src/data/segments.py`;
without it the segment is derived from a BDG2 `primary_use`, which is also how LEAD buildings
get theirs. Missing coordinates fall back to Istanbul (`DEFAULT_LAT`, `DEFAULT_LNG`,
`DEFAULT_TIMEZONE`). The timezone defaults to the default timezone and the country is derived
from it. The building gets a `weather_key` of `geo:<lat>,<lng>` rounded to 2 decimals, about
1 km, so buildings on the same campus share one weather series. Registering again updates the
fields that are given and keeps the stored ones, including the location and the segment.

### Operating hours and planned downtime

`PUT /buildings/{id}/operating-hours` stores the weekly schedule as periods per ISO weekday
(1 = Monday); a factory with shifts sends one period per shift, and a period that closes at
or before it opens runs past midnight; periods may not overlap. `POST /buildings/{id}/downtime` adds a holiday,
maintenance or shutdown window in building local time. The models do not use either yet;
they are stored so rules and thresholds can tell a planned quiet day from waste.

### Import a meter export

```bash
python -m src.data.customer import --external-id acme-hq --file export.csv \
    --ts-col timestamp --value-col kwh --unit kwh [--cumulative] [--source-tz UTC]
```

or `POST /buildings/{id}/readings/import`. `src/data/validate.py` normalizes the export:

- Separator and decimal: separator is sniffed, `--decimal ,` for Turkish exports.
- Timestamps: ISO offsets are honored; naive timestamps are read in `--source-tz` or the
  building timezone. The repeated hour at the autumn DST change is resolved by order;
  nonexistent spring hours are dropped. Mixing offset and naive values is rejected.
- Units: `wh`, `kwh`, `mwh`, `kbtu`.
- Cumulative registers: differenced; a decrease is a register reset and counts as usage since
  the reset; differences across gaps are dropped.
- Interval: 15-minute and other sub-hourly data are summed into hours. Each hour gets a
  quality flag: `ok`, `partial` (fewer intervals than expected), `merged` (more) or `missing`.
  Partial and merged hours are kept in `readings` but count as missing for the models.
- Cleaning: negative values become missing, exact duplicates are dropped, conflicting
  duplicates keep the last value, readings more than an hour in the future are dropped, and
  data coarser than hourly is rejected.

The response and the CLI print an `ImportReport` with counts for every case. Readings are
upserted, so re-importing an overlapping file is safe. After the import, Open-Meteo weather
is fetched for the imported period: the archive API for days older than five days and the
forecast API for the last five, so a period that spans both is split in two requests. A failed
weather call is logged and can be retried later.

### Live meters over MQTT

Topic `meters/<external_id>`, payload `{"ts": "2026-10-19T14:00:00+03:00", "kwh": 12.5}`: the
energy used in the hour that starts at `ts`. `src.ingest.mqtt_ingest` validates messages,
buffers them for 5 seconds and upserts them. Unknown buildings and future timestamps are
dropped and logged. `python -m src.ingest.replay --split test --register` replays LEAD
buildings as live Istanbul customers for demos, all buildings of one hour together; see
[backend.md](backend.md#virtual-meters-and-the-live-demo).

### Feedback as labels

Operators mark events as `confirmed`, `false_alarm`, `planned`, `sent` or `applied`
(`POST /anomalies/{id}/feedback`). `src/data/feedback.py` turns verdicts into hourly labels:
confirmed and applied are 1, false alarm and planned are 0, sent labels nothing, and the
latest labeling verdict wins. `python -m src.models.train --source db` trains on
LEAD labels plus this feedback.

### Demo tenants

`data/meter_catalog.csv` turns the 30 LEAD buildings with anomalies into five demo
customers. Each row maps one building to a tenant, its plan, a segment, the location used as
the building name, an asset and the asset's equipment type. An optional
`tariff_tl_per_kwh` column sets the building tariff; without it `TARIFF_TL_PER_KWH` applies.

```bash
python -m src.data.catalog [--file data/meter_catalog.csv]
```

The catalog is checked first (columns, equipment types from the `core.assets` CHECK,
segments, one plan per tenant, no repeated location within a tenant) and every building must
already be loaded; otherwise nothing is written. The run then registers the tenants with their
plan, sets each building's tenant, name and segment without touching its location or weather,
creates one asset per building (`zone` for `genel`, `machine` for the rest) and points the
building's main meter at it. With `DEMO_TENANT_PASSWORD` set, every tenant gets a demo user
`<tenant name in ASCII>@enerji.local`, e.g. `otel@enerji.local`; the role follows the plan:

| Plan | Role |
|---|---|
| `izleme` | `viewer` |
| `operasyon`, `analiz`, `uzman`, `kurumsal` | `operator` |

The plan is stored on the tenant only; no feature is gated by it yet. Running the command again
updates the same rows and keeps existing passwords.

## Weather and seasonality

Load depends on outdoor temperature through heating and cooling, so features include
temperature, dew point, wind, cloud cover, heating and cooling degree hours (base 18 °C), the
weekly mean temperature and the deviation from it. Calendar features include public holidays
per country and the half-day Bayram eves in Turkey. Notebook 01 plots monthly load against
temperature.

## Rules for everyone

- `data/raw` and `data/processed` are never committed; `data/splits.json` is.
- Do not change `splits.json` without telling the ML developer; every reported number depends
  on it.
- Schema changes go into `db/init/001_schema.sql` and must stay idempotent.
