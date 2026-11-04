# API

FastAPI app in `src/api/main.py`. Interactive docs at `http://localhost:8000/docs` and the
OpenAPI schema at `/openapi.json`. Request and response bodies are defined in
`src/api/schemas.py`; the dashboard calls them through `dashboard/api_client.py`.

```bash
uvicorn src.api.main:app --reload        # demo data without DATABASE_URL
```

## Modes

| `DATABASE_URL` | Mode | Reads | Writes |
|---|---|---|---|
| set | `database` | Postgres, signed-in users only | by role, see below |
| not set | `demo` | two built-in Istanbul buildings, no login | 503 |

Timestamps are the building's local time without an offset, for example
`2026-10-19T01:00:00`.

## Login, roles and tenants

The dashboard signs in with `POST /auth/login`; the answer sets the session cookie
`energy_session` (HttpOnly, `SameSite=Lax`, `Secure` when `COOKIE_SECURE=true`, valid 7 days).
Every endpoint except `/health` and `/auth/login` needs it. Create the first user with
`python -m src.api.users create --email you@example.com --role admin`.

| Role | Can |
|---|---|
| `viewer` | read |
| `operator` | read, give feedback, import readings, edit operating hours and downtime |
| `admin` | all of the above, register buildings, create users |

A user belongs to one tenant, or to none (platform staff). Tenant users see only their
tenant's buildings, anomalies, summary and users; another tenant's building answers 404 as if
it did not exist. Platform staff see everything and alone can register tenants. A tenant admin
always registers buildings and users into the own tenant; `tenant_id` in the body is ignored.

Every request other than `GET` needs the header `X-Requested-With` (any value). Browsers only
send it after a CORS preflight, so a page on another site cannot submit a form with the
user's cookie. After 5 failed logins for one e-mail in 15 minutes, logins for it return 429.

| Method and path | Body | Returns |
|---|---|---|
| `POST /auth/login` | `email`, `password` | the user (`user_id`, `tenant_id`, `email`, `name`, `role`) and the cookie |
| `POST /auth/logout` | | 204; the session is deleted |
| `GET /auth/me` | | the signed-in user |
| `GET /users` | | users in scope (admin) |
| `POST /users` | `email`, `name`, `role`, `password` (12+ characters), `tenant_id` | 201 and `{"id": <user_id>}`; 409 for a taken e-mail (admin) |

```bash
curl -c jar.txt -X POST http://localhost:8000/auth/login -H "X-Requested-With: curl" \
  -H "Content-Type: application/json" -d '{"email": "you@example.com", "password": "..."}'
curl -b jar.txt http://localhost:8000/buildings
```

## Read endpoints

Any signed-in role.

| Method and path | Query | Returns |
|---|---|---|
| `GET /health` | | `{"status": "ok", "mode": "demo" \| "database"}`; `mode` tells which repository serves the data |
| `GET /buildings` | | active buildings with `segment`, `tariff_tl_per_kwh` (the meter's own or the default), `tenant_name`, `asset_name`, `equipment_type`, `last_scored_ts` and `anomaly_count` |
| `GET /buildings/{id}/operating-hours` | | weekly periods `weekday`, `opens`, `closes` |
| `GET /buildings/{id}/downtime` | | planned downtime windows, earliest first |
| `GET /buildings/{id}/readings` | `hours` 1-4320, default 48; `end` optional | hourly `kwh` and label `anomaly` |
| `GET /buildings/{id}/forecast` | `hours` 1-4320, default 48; `end` optional | `expected_kwh`, `alarm`, `source`, `origin` |
| `GET /anomalies` | filters below, `limit` 1-500, default 50 | events, newest first, with the latest `verdict` (feedback whose event was re-published still applies by time range) and a `recommendation` |
| `GET /recommendations` | | the recommendation table: `equipment_type`, `anomaly_type`, `title`, `action` |
| `GET /summary` | `customers_only` optional | `total_cost_tl`, `active_alerts`, `worst_building_id`, `buildings`; `customers_only=true` counts only buildings that belong to a business |
| `GET /tenants` | | `tenant_id`, `name`, `status`, `city`; tenant users see their own |
| `GET /tenants/summary` | `days` 1-365, default 30 | each business: `waste_tl`, `waste_kwh`, `open_alerts`, `data_issues` and its `meters` with the same fields, costliest first |
| `GET /tenants/{id}/summary` | `days` 1-365, default 30 | one business, as above; 404 outside your scope |
| `GET /live-demo` | | the live demo switch: `available`, `running`, `started_at`, `sim_ts`, `tenant_id` and the live meters you may see with their last reading |

- The `hours` window ends at the building's latest reading, not at the current time, so a
  building that stopped sending data still shows its last period. `end` (building local time
  without an offset, e.g. `2016-12-30T05:00:00`) ends it at that hour instead, to look at an
  older anomaly.
- Once the scoring service covers a building, `forecast`, `anomalies`, `anomaly_count` and
  `summary` use its live output only; before that they show the batch output from training.
- `active_alerts` counts events that ended within 24 hours of the building's latest reading.
- `summary` covers active buildings only, the same set as `GET /buildings`.
- Tax numbers and contacts are not returned by any endpoint.
- Anomalies carry `category`: `waste` (consumption above expected, with `excess_kwh` and
  `est_cost_tl`) or `data_issue` (stuck or silent meter; both cost fields `null`).
- Anomalies carry `explanation` (built-in Turkish text) and `advice` (Turkish advice from
  Claude when `ANTHROPIC_API_KEY` is set; `null` until written, empty when the model declined).
- Anomalies also carry `building_name`, `tenant_id`, `asset_name`, `equipment_type` and
  `recommendation` (`title`, `action`): the row of `data/recommendations.csv` for the meter's
  equipment and the anomaly type (the rule, or `model` when only a model fired), falling back
  to `*` (any equipment) and then `*,*`. Edit that file to change the texts; no code change.
- `GET /anomalies` filters, all optional: `building_id`, `tenant_id`, `category` (`waste`,
  `data_issue`), `status`, `equipment_type`, `since` and `until` (events that overlap the
  window, local time), `min_cost_tl` and `order` (`recent` or `cost`). `status` is a verdict
  or `new` (no verdict), `open` (not closed) or `closed` (`applied`, `false_alarm`, `planned`).
- Tenant summaries look at each meter's last `days` days of data, ending at its latest
  reading. `open_alerts` counts waste events in that window that are not closed.

```bash
curl -b jar.txt "http://localhost:8000/buildings/100000/readings?hours=24"
curl -b jar.txt "http://localhost:8000/anomalies?building_id=100000&limit=10"
curl -b jar.txt "http://localhost:8000/anomalies?tenant_id=3&status=open&category=waste&order=cost&limit=3"
curl -b jar.txt "http://localhost:8000/tenants/summary?days=30"
```

## Write endpoints

Writes need the session cookie, the `X-Requested-With` header and the role named in each
section. The examples reuse `jar.txt` from the login above.

### `POST /tenants`

Platform admin. Registers a tenant, or updates the given fields when the `name` already exists. Returns 201
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
  -b jar.txt -H "X-Requested-With: curl" -H "Content-Type: application/json" \
  -d '{"name": "Acme A.S.", "tax_number": "1234567890", "city": "Istanbul"}'
```

### `POST /buildings`

Admin. Registers a building, or updates it when `external_id` already exists. Returns 201 and
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
  -b jar.txt -H "X-Requested-With: curl" -H "Content-Type: application/json" \
  -d '{"external_id": "acme-hq", "name": "Acme HQ", "lat": 41.01, "lng": 28.98,
       "primary_use": "Office", "square_feet": 54000, "tariff_tl_per_kwh": 4.2}'
```

### `PUT /buildings/{id}/operating-hours`

Operator or admin. Replaces the weekly schedule and returns it. The body is a list of up to 70 periods; an empty
list clears the schedule. Periods may not overlap, also across midnight and from Sunday into
Monday; one may start when another ends.

| Field | Rule |
|---|---|
| `weekday` | 1-7, ISO weekday (1 = Monday) |
| `opens`, `closes` | `HH:MM`; `closes` at or before `opens` runs past midnight |

```bash
curl -X PUT http://localhost:8000/buildings/100000/operating-hours \
  -b jar.txt -H "X-Requested-With: curl" -H "Content-Type: application/json" \
  -d '[{"weekday": 1, "opens": "06:00", "closes": "14:00"},
       {"weekday": 1, "opens": "22:00", "closes": "06:00"}]'
```

### `POST /buildings/{id}/downtime` and `DELETE /buildings/{id}/downtime/{downtime_id}`

Operator or admin. Adds a planned downtime window and returns 201 with `{"id": <downtime_id>}`; `DELETE` removes
one and returns 204.

| Field | Rule |
|---|---|
| `starts_at`, `ends_at` | building local time without an offset; `ends_at` after `starts_at` |
| `kind` | `holiday`, `maintenance` or `shutdown` |
| `note` | up to 500 characters |

### `POST /buildings/{id}/readings/import`

Operator or admin. Uploads a meter export as `multipart/form-data`. The cleaning rules are in
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
  -b jar.txt -H "X-Requested-With: curl" \
  -F file=@export.csv -F ts_col=Tarih -F value_col=Tuketim -F decimal=,
```

### `POST /anomalies/{id}/feedback`

Operator or admin. Stores a verdict with the signed-in user's e-mail as `created_by`. Returns 201 and `{"id": <feedback_id>}`. Verdicts become training
labels (see [data.md](data.md#feedback-as-labels)).

| Field | Rule |
|---|---|
| `verdict` | `confirmed`, `false_alarm` or `planned` judge the alarm; `sent` (recommendation told to the business) and `applied` (the business acted on it) follow the recommendation |
| `note` | up to 1000 characters |

```bash
curl -X POST http://localhost:8000/anomalies/1/feedback \
  -b jar.txt -H "X-Requested-With: curl" -H "Content-Type: application/json" \
  -d '{"verdict": "confirmed", "note": "chiller left on"}'
```

### `POST /live-demo/start` and `POST /live-demo/stop`

Platform admin only, no body; both return the same object as `GET /live-demo`. Every start
wipes the previous run of the live meters and plays from the beginning; stop only stops
sending. The `live-demo` service does the work ([backend.md](backend.md#live-demo-panel-button)).

## Errors

Errors return `{"detail": "..."}`.

| Status | When |
|---|---|
| 401 | not signed in, session expired, or wrong e-mail or password at login |
| 403 | role too low, or `X-Requested-With` missing on a write |
| 404 | building, tenant, anomaly or downtime not found or outside the user's tenant, or a building without readings |
| 409 | tax number used by another tenant, e-mail taken, or an `external_id` that belongs to another tenant |
| 413 | upload larger than 20 MB |
| 422 | invalid body or query, an id outside its Postgres integer range, or an export that cannot be parsed |
| 429 | too many failed logins for this e-mail |
| 503 | a write or login in demo mode |

## CORS

Allowed origins come from `CORS_ORIGINS` (comma separated, default
`http://localhost:5173,http://127.0.0.1:5173`). Credentials (the cookie) are allowed for those
origins only. Methods `GET`, `POST`, `PUT` and `DELETE`, headers `Content-Type` and
`X-Requested-With`.

## Changing the API

1. Add or change the model in `src/api/schemas.py`.
2. Add the query to `Repository`, `PostgresRepository` and `DemoRepository` in
   `src/api/repository.py`.
3. Add the route in `src/api/main.py` with the repository from `Repo` (already limited to the
   user's tenant); writes get `dependencies=Writer` or an `Admin` parameter.
4. Update `dashboard/api_client.py` when the dashboard needs it, this file and
   `tests/test_api.py`.
