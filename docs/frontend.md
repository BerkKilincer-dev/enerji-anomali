# Frontend

Owner: frontend developer. A small React dashboard in `frontend/` that reads the API
([api.md](api.md)) and lets an operator give feedback on alarms.

| Item | Choice |
|---|---|
| Build | Vite 8, TypeScript 7 (strict) |
| UI | React 19, plain CSS (`src/styles.css`) |
| Charts | ECharts 6, tree-shaken imports |
| Routing | URL hash: `#/buildings/<id>` |
| State | `useAsync` hook per request, no global store |

## Running

```bash
cd frontend
cp .env.example .env        # VITE_API_URL, default http://localhost:8000
npm install
npm run dev                 # http://127.0.0.1:5173
npm run build               # type check and production bundle in dist/
```

The API without `DATABASE_URL` serves demo data, so the dashboard works without a database. A
"demo data" tag appears in the header in that mode. In Docker Compose the `frontend` service
runs the dev server.

## Structure

| File | Role |
|---|---|
| `src/api.ts` | typed client; types mirror `src/api/schemas.py` |
| `src/App.tsx` | header, API key field, overview or building detail |
| `src/components/Overview.tsx` | summary cards and the building table |
| `src/components/BuildingDetail.tsx` | range picker, chart, anomaly table |
| `src/components/EnergyChart.tsx` | actual against expected kWh, alarm periods shaded; the chart is created once and updated when data changes |
| `src/components/AnomalyTable.tsx` | events with cost, explanation and feedback buttons |
| `src/components/ApiKeyField.tsx` | stores the key used for feedback |
| `src/format.ts` | TL, kWh and time formatting, building and rule labels |
| `src/useAsync.ts` | data, error and reload for one request |
| `src/components/Async.tsx` | shows loading, the error or the loaded content of a `useAsync` state |

## Conventions

- All API calls go through `src/api.ts`; change the types there whenever the backend schema
  changes.
- Timestamps from the API are building local time without an offset. Show them as they are
  and never convert them with the browser timezone.
- Money is shown in TL with `tr-TR` formatting, energy in kWh.
- Keep components small and typed; no `any`.
- `npm run build` must pass before a PR.

## Phones

Below 640 px the padding shrinks, the cards go two per row, the API key field takes the full
width and the chart is 300 px high. Tables sit in `.table-wrap` and scroll sideways instead of
widening the page; timestamps (`.nowrap`) stay on one line. Check a change at 375 px width
(browser device mode): the page must not scroll horizontally.

## API key

Feedback buttons need the API key. It is typed into the header field and kept in
`localStorage`, which is fine for a local or internal tool but not for customers: the
production plan is a login with a server-side session (see [security.md](security.md)).

## Next steps

See [roadmap.md](roadmap.md#frontend-developer): building registration and CSV upload forms, a cost
trend, alarm filters and Turkish labels.
