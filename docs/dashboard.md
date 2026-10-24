# Dashboard

Owner: frontend developer. The Streamlit CRM panel in `dashboard/`: a business signs in, sees
its meters, their consumption against the expected level, the anomalies with their TL cost and
advice, gives feedback on each alarm and exports reports. It reads everything through the API
([api.md](api.md)); the session cookie stays on the Streamlit server, never in the browser.
The panel says bina (building) where the CRM plan says meter (sayaç); see the README.

| Item | Choice |
|---|---|
| Framework | Streamlit (pinned in `dashboard/requirements.txt`) |
| Charts | matplotlib, rendered as images |
| Styling | `dashboard/style.css` plus inline CSS; it targets Streamlit's own class names, so upgrade Streamlit only on purpose and check every page |
| Exports | CSV, Excel (openpyxl), PDF (matplotlib) in `dashboard/reports.py` |

## Running

`docker compose up -d --build` starts it as the `dashboard` service on http://localhost:5173.
The folder is mounted into the container and Streamlit polls for changes, so an edited file
shows after a browser refresh; a new package in `requirements.txt` needs `--build`.

## Structure

| File | Role |
|---|---|
| `app.py` | login, sidebar and the pages: Genel Bakış, Tüketim Analizi, Anomaliler, Raporlar, Ayarlar |
| `api_client.py` | API client; one `requests.Session` per signed-in user keeps the cookie |
| `charts.py` | consumption and donut charts, Turkish date formats |
| `reports.py` | report data and the CSV, Excel and PDF exports |
| `icons.py` | SVG icons and the logo |
| `style.css` | look and feel |

## Conventions

- Every API call goes through `api_client.py`.
- Costs come from the API: anomalies carry `est_cost_tl`; consumption costs use each meter's
  `tariff_tl_per_kwh` from `GET /buildings`. `TARIFF_TL` in `app.py` is only the fallback.
- Anomalies with `category == "data_issue"` (stuck or silent meter) have no cost; show them
  apart from waste.
- Timestamps from the API are building local time without an offset; never shift them.
- Cached loaders take `user_id` in their key, so two users never share data.
- CI runs `app.py` headless and checks that the login page renders.
