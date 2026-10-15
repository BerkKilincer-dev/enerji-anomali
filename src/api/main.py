"""Enerji Anomali API.

Şimdilik SAHTE veri döndürür; Frontend bununla çalışmaya başlayabilir.
Backend, uç noktaları sırayla gerçek veritabanı sorgularıyla değiştirir.
Uç nokta adları ve dönen alanlar ilk hafta kararlaştırılan sözleşmedir.
"""

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI

app = FastAPI(title="Enerji Anomali API", version="0.1.0")

_NOW = datetime(2016, 6, 1, tzinfo=timezone.utc)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/buildings")
def list_buildings():
    return [
        {"building_id": 1, "primary_use": "Office", "square_feet": 50000},
        {"building_id": 2, "primary_use": "Retail", "square_feet": 120000},
    ]


@app.get("/buildings/{building_id}/readings")
def readings(building_id: int, hours: int = 48):
    return [
        {"building_id": building_id, "ts": (_NOW + timedelta(hours=h)).isoformat(),
         "kwh": 100 + (40 if 8 <= h % 24 <= 18 else 0)}
        for h in range(hours)
    ]


@app.get("/buildings/{building_id}/forecast")
def forecast(building_id: int, hours: int = 48):
    return [
        {"building_id": building_id, "ts": (_NOW + timedelta(hours=h)).isoformat(),
         "expected_kwh": 95 + (40 if 8 <= h % 24 <= 18 else 0)}
        for h in range(hours)
    ]


@app.get("/anomalies")
def anomalies(building_id: int | None = None):
    items = [
        {"id": 1, "building_id": 2, "ts_start": "2016-06-02T01:00:00+00:00",
         "ts_end": "2016-06-02T05:00:00+00:00", "score": 0.91, "source": "rule",
         "rule_type": "gece_taban_yuk", "excess_kwh": 180.0, "est_cost_tl": 630.0,
         "explanation": "Gece 01:00-05:00 arası taban yük normalin %45 üzerinde."},
    ]
    if building_id is not None:
        items = [a for a in items if a["building_id"] == building_id]
    return items


@app.get("/summary")
def summary():
    return {"total_cost_tl": 630.0, "active_alerts": 1, "worst_building_id": 2}
