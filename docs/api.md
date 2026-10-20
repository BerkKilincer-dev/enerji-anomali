# API

FastAPI app in `src/api/main.py`. Interactive docs at `http://localhost:8000/docs` and the
OpenAPI schema at `/openapi.json`. Request and response bodies are defined in
`src/api/schemas.py`; the frontend mirrors them in `frontend/src/api.ts`.

```bash
uvicorn src.api.main:app --reload        # demo data without DATABASE_URL
```

## Modes

| `DATABASE_URL` | Mode | Reads | Writes |
|---|---|---|---|
| set | `database` | Postgres | allowed with `X-API-Key` |
| not set | `demo` | two built-in Istanbul buildings | 503 |

Timestamps are the building's local time without an offset, for example
`2026-10-19T01:00:00`.

## Read endpoints

No key needed.

| Method and path | Query | Returns |
|---|---|---|
| `GET /health` | | `{"status": "ok", "mode": "demo" \| "database"}`; `mode` tells which repository serves the data |
| `GET /buildings` | | active buildings with `segment`, `last_scored_ts` and `anomaly_count` |
| `GET /buildings/{id}/operating-hours` | | weekly periods `weekday`, `opens`, `closes` |
| `GET /buildings/{id}/downtime` | | planned downtime windows, earliest first |
| `GET /buildings/{id}/readings` | `hours` 1-2160, default 48 | hourly `kwh` and label `anomaly` |
| `GET /buildings/{id}/forecast` | `hours` 1-2160, default 48 | `expected_kwh`, `alarm`, `source`, `origin` |
| `GET /anomalies` | `building_id` optional, `limit` 1-500, default 50 | events, newest first, with the latest `verdict` (feedback whose event was re-published still applies by time range) |
| `GET /summary` | | `total_cost_tl`, `active_alerts`, `worst_building_id`, `buildings` |

- The `hours` window ends at the building's latest reading, not at the current time, so a
  building that stopped sending data still shows its last period.
- Once the scoring service covers a building, `forecast`, `anomalies`, `anomaly_count` and
  `summary` use its live output only; before that they show the batch output from training.
- `active_alerts` counts events that ended within 24 hours of the building's latest reading.
- `summary` covers active buildings only, the same set as `GET /buildings`.
- Tenant details (tax number, contacts) have no read endpoint; they stay out of the
  unauthenticated API until login exists.

```bash
curl "http://localhost:8000/buildings/100000/readings?hours=24"
curl "http://localhost:8000/anomalies?building_id=100000&limit=10"
```

## Write endpoints

Every write needs the header `X-API-Key: <API_KEY>`. Without `API_KEY` on the server, writes
are switched off and return 503.

### `POST /tenants`

Registers a tenant, or updates the given fields when the `name` already exists. Returns 201
and `{"id": <tenant_id>}`; 409 when another tenant has the same tax number.

| Field | Rule |
|---|---|
| `name` | required, 1-200 characters |
| `tax_number` | 10 digits, or 11 for a sole proprietor |
| `tax_office`, `city` | up to 100 characters |
| `address` | up to 500 characters |
| `contact_name` | up to 200 characters |
| `contact_email` | an email address |
| `contact_phone` | 7-20 digits, spaces, `+`, `-` or parentheses |
| `plan` | up to 50 characters |
| `status` | `pilot`, `active` or `inactive`; new tenants start as `pilot` |
| `dpa_signed_on` | date the data processing agreement was signed, `YYYY-MM-DD` |

```bash
curl -X POST http://localhost:8000/tenants \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "Acme A.S.", "tax_number": "1234567890", "city": "Istanbul"}'
```

### `POST /buildings`

Registers a building, or updates it when `external_id` already exists. Returns 201 and
`{"id": <building_id>}`.

| Field | Rule |
|---|---|
| `external_id` | required, `[A-Za-z0-9._-]`, 1-64 characters |
| `name` | up to 200 characters |
| `lat`, `lng` | valid coordinates; missing means `DEFAULT_LAT` and `DEFAULT_LNG` (Istanbul) |
| `timezone` | IANA name such as `Europe/Istanbul`; missing means `DEFAULT_TIMEZONE` |
| `country` | two letters; derived from the timezone when missing |
| `primary_use` | up to 100 characters, for example `Office` |
| `square_feet` | greater than 0 |
| `year_built` | 1800-2100 |
| `tariff_tl_per_kwh` | 0 or more; missing means `TARIFF_TL_PER_KWH` |
| `tenant_id` | an existing tenant, 404 otherwise |
| `segment` | one of `office`, `mall`, `retail`, `hotel`, `hospital`, `education`, `factory`, `warehouse`, `data_center`, `residential`, `public`, `other`; derived from a BDG2 `primary_use` when missing |

```bash
curl -X POST http://localhost:8000/buildings \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"external_id": "acme-hq", "name": "Acme HQ", "lat": 41.01, "lng": 28.98,
       "primary_use": "Office", "square_feet": 54000, "tariff_tl_per_kwh": 4.2}'
```

### `PUT /buildings/{id}/operating-hours`

Replaces the weekly schedule and returns it. The body is a list of up to 70 periods; an empty
list clears the schedule. Periods may not overlap, also across midnight and from Sunday into
Monday; one may start when another ends.

| Field | Rule |
|---|---|
| `weekday` | 1-7, ISO weekday (1 = Monday) |
| `opens`, `closes` | `HH:MM`; `closes` at or before `opens` runs past midnight |

```bash
curl -X PUT http://localhost:8000/buildings/100000/operating-hours \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '[{"weekday": 1, "opens": "06:00", "closes": "14:00"},
       {"weekday": 1, "opens": "22:00", "closes": "06:00"}]'
```

### `POST /buildings/{id}/downtime` and `DELETE /buildings/{id}/downtime/{downtime_id}`

Adds a planned downtime window and returns 201 with `{"id": <downtime_id>}`; `DELETE` removes
one and returns 204.

| Field | Rule |
|---|---|
| `starts_at`, `ends_at` | building local time without an offset; `ends_at` after `starts_at` |
| `kind` | `holiday`, `maintenance` or `shutdown` |
| `note` | up to 500 characters |

### `POST /buildings/{id}/readings/import`

Uploads a meter export as `multipart/form-data`. The cleaning rules are in
[data.md](data.md#import-a-meter-export).

| Form field | Default | Meaning |
|---|---|---|
| `file` | required | CSV export, at most 20 MB |
| `ts_col` | `timestamp` | timestamp column |
| `value_col` | `kwh` | value column |
| `unit` | `kwh` | `wh`, `kwh`, `mwh` or `kbtu` |
| `cumulative` | `false` | `true` for a register that only counts up |
| `source_tz` | building timezone | timezone of naive timestamps |
| `sep` | sniffed | column separator |
| `decimal` | `.` | `,` for Turkish exports |

Returns the import report: `rows_in`, `unparseable`, `duplicates`, `conflicting_duplicates`,
`negative`, `resets`, `gap_diffs`, `future`, `step_minutes`, `hours_out`, `partial_hours`,
`first_ts`, `last_ts`.

```bash
curl -X POST http://localhost:8000/buildings/100000/readings/import \
  -H "X-API-Key: $API_KEY" \
  -F file=@export.csv -F ts_col=Tarih -F value_col=Tuketim -F decimal=,
```

### `POST /anomalies/{id}/feedback`

Stores an operator verdict. Returns 201 and `{"id": <feedback_id>}`. Verdicts become training
labels (see [data.md](data.md#feedback-as-labels)).

| Field | Rule |
|---|---|
| `verdict` | `confirmed`, `false_alarm` or `planned` |
| `note` | up to 1000 characters |
| `created_by` | up to 100 characters |

```bash
curl -X POST http://localhost:8000/anomalies/1/feedback \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"verdict": "confirmed", "note": "chiller left on", "created_by": "ops"}'
```

## Errors

Errors return `{"detail": "..."}`.

| Status | When |
|---|---|
| 401 | `X-API-Key` missing or wrong |
| 404 | building, tenant, anomaly or downtime not found, or a building without readings |
| 409 | tax number already used by another tenant |
| 413 | upload larger than 20 MB |
| 422 | invalid body or query, an id outside its Postgres integer range, or an export that cannot be parsed |
| 503 | `API_KEY` not set, or a write in demo mode |

## CORS

Allowed origins come from `CORS_ORIGINS` (comma separated, default
`http://localhost:5173,http://127.0.0.1:5173`). Methods `GET`, `POST`, `PUT` and `DELETE`, headers
`Content-Type` and `X-API-Key`.

## Changing the API

1. Add or change the model in `src/api/schemas.py`.
2. Add the query to `Repository`, `PostgresRepository` and `DemoRepository` in
   `src/api/repository.py`.
3. Add the route in `src/api/main.py`; writes get `dependencies=Writer`.
4. Update the types in `frontend/src/api.ts`, this file and `tests/test_api.py`.
