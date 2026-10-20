"""HTTP API for the dashboard and integrations.

Run: uvicorn src.api.main:app --reload
Reads come from Postgres when DATABASE_URL is set, otherwise from built-in demo data.
Write endpoints need an X-API-Key header that matches API_KEY; without API_KEY they are off.
"""

from __future__ import annotations

import hmac
import io
from functools import lru_cache
from typing import Annotated

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api import schemas
from src.api.repository import (
    DemoRepository,
    NotFoundError,
    PostgresRepository,
    ReadOnlyError,
    Repository,
)
from src.config import get_settings
from src.data.customer import BuildingInfo, read_export

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_HOURS = 24 * 90

app = FastAPI(title="Energy Anomaly API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(get_settings().cors_origins),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)


@lru_cache(maxsize=1)
def get_repository() -> Repository:
    url = get_settings().database_url
    return PostgresRepository(url) if url else DemoRepository()


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    expected = get_settings().api_key
    if not expected:
        raise HTTPException(503, "writes are disabled: API_KEY is not set")
    if not x_api_key or not hmac.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(401, "missing or invalid X-API-Key")


Repo = Annotated[Repository, Depends(get_repository)]
Hours = Annotated[int, Query(ge=1, le=MAX_HOURS)]
Writer = [Depends(require_api_key)]


@app.exception_handler(NotFoundError)
def not_found(_, exc: NotFoundError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(ReadOnlyError)
def read_only(_, exc: ReadOnlyError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.get("/health")
def health(repo: Repo) -> dict:
    return {"status": "ok", "mode": repo.mode}


@app.get("/buildings")
def buildings(repo: Repo) -> list[schemas.Building]:
    return repo.buildings()


@app.get("/buildings/{building_id}/readings")
def readings(building_id: int, repo: Repo, hours: Hours = 48) -> list[schemas.Reading]:
    return repo.readings(building_id, hours)


@app.get("/buildings/{building_id}/forecast")
def forecast(building_id: int, repo: Repo, hours: Hours = 48) -> list[schemas.ForecastPoint]:
    return repo.forecast(building_id, hours)


@app.get("/anomalies")
def anomalies(
    repo: Repo, building_id: int | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 50
) -> list[schemas.Anomaly]:
    return repo.anomalies(building_id, limit)


@app.get("/summary")
def summary(repo: Repo) -> schemas.Summary:
    return repo.summary()


@app.post("/buildings", status_code=201, dependencies=Writer)
def register(body: schemas.BuildingIn, repo: Repo) -> schemas.Created:
    return schemas.Created(id=repo.register(BuildingInfo(**body.model_dump())))


@app.post("/buildings/{building_id}/readings/import", dependencies=Writer)
def import_readings(
    building_id: int,
    repo: Repo,
    file: Annotated[UploadFile, File()],
    ts_col: Annotated[str, Form()] = "timestamp",
    value_col: Annotated[str, Form()] = "kwh",
    unit: Annotated[str, Form()] = "kwh",
    cumulative: Annotated[bool, Form()] = False,
    source_tz: Annotated[str | None, Form()] = None,
    sep: Annotated[str | None, Form()] = None,
    decimal: Annotated[str, Form()] = ".",
) -> schemas.ImportResult:
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
    try:
        raw = read_export(io.BytesIO(content), ts_col, value_col, sep, decimal)
        report = repo.import_readings(building_id, raw, unit, cumulative, source_tz)
    except (ValueError, KeyError, pd.errors.ParserError) as exc:
        raise HTTPException(422, f"cannot import file: {exc}") from exc
    return schemas.ImportResult(**report.as_dict())


@app.post("/anomalies/{anomaly_id}/feedback", status_code=201, dependencies=Writer)
def feedback(anomaly_id: int, body: schemas.FeedbackIn, repo: Repo) -> schemas.Created:
    return schemas.Created(
        id=repo.add_feedback(anomaly_id, body.verdict, body.note, body.created_by)
    )
