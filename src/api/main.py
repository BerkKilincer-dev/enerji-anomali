"""HTTP API for the dashboard and integrations.

Run: uvicorn src.api.main:app --reload
Reads come from Postgres when DATABASE_URL is set, otherwise from built-in demo data.
With a database every endpoint except /health and /auth/login needs a signed-in user
(session cookie from POST /auth/login). Viewers read, operators also give feedback and import
data, admins also register buildings and users; users without a tenant are platform staff and
see every tenant. Writes need the header X-Requested-With, which a cross-site form cannot send.
"""

from __future__ import annotations

import io
from functools import lru_cache
from typing import Annotated

import pandas as pd
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import NaiveDatetime

from src.api import auth, schemas
from src.api.auth import User
from src.api.repository import (
    ConflictError,
    DemoRepository,
    NotFoundError,
    PostgresRepository,
    ReadOnlyError,
    Repository,
)
from src.config import get_settings
from src.data.customer import BuildingInfo, TenantInfo, read_export

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_HOURS = 24 * 90
# Demo mode has no accounts; this user lets the dashboard read and shows writes as unavailable.
DEMO_USER = User(0, None, "demo@localhost", "Demo", "admin")


def same_site_request(
    request: Request, x_requested_with: Annotated[str | None, Header()] = None
) -> None:
    # A custom header forces a CORS preflight, which only the allowed origins pass.
    if request.method not in ("GET", "HEAD", "OPTIONS") and not x_requested_with:
        raise HTTPException(403, "missing X-Requested-With header")


app = FastAPI(
    title="Energy Anomaly API", version="0.3.0", dependencies=[Depends(same_site_request)]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(get_settings().cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-Requested-With"],
)
throttle = auth.LoginThrottle()


@lru_cache(maxsize=1)
def get_repository() -> Repository:
    url = get_settings().database_url
    return PostgresRepository(url) if url else DemoRepository()


Base = Annotated[Repository, Depends(get_repository)]
SessionCookie = Annotated[str | None, Cookie(alias=auth.COOKIE)]


def current_user(base: Base, session: SessionCookie = None) -> User:
    if base.mode == "demo":
        return DEMO_USER
    user = base.session_user(auth.token_hash(session)) if session else None
    if user is None:
        raise HTTPException(401, "not signed in")
    return user


Viewer = Annotated[User, Depends(current_user)]


def scoped_repository(base: Base, user: Viewer) -> Repository:
    return base.scoped(user.tenant_id)


def require_operator(user: Viewer) -> User:
    if not user.can_write:
        raise HTTPException(403, "operator or admin role required")
    return user


def require_admin(user: Viewer) -> User:
    if not user.is_admin:
        raise HTTPException(403, "admin role required")
    return user


def require_platform_admin(user: Annotated[User, Depends(require_admin)]) -> User:
    if not user.is_platform:
        raise HTTPException(403, "platform admin required")
    return user


Repo = Annotated[Repository, Depends(scoped_repository)]
Operator = Annotated[User, Depends(require_operator)]
Admin = Annotated[User, Depends(require_admin)]
Hours = Annotated[int, Query(ge=1, le=MAX_HOURS)]
# Last hour of a window, building local time; without it the window ends at the latest reading.
End = Annotated[NaiveDatetime | None, Query()]
# Ids outside the Postgres INT and BIGINT ranges are rejected before they reach SQL.
BuildingId = Annotated[int, Path(ge=0, le=schemas.INT_MAX)]
RowId = Annotated[int, Path(ge=1, le=schemas.BIGINT_MAX)]
Writer = [Depends(require_operator)]


@app.exception_handler(NotFoundError)
def not_found(_, exc: NotFoundError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(ConflictError)
def conflict(_, exc: ConflictError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(ReadOnlyError)
def read_only(_, exc: ReadOnlyError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.get("/health")
def health(base: Base) -> dict:
    return {"status": "ok", "mode": base.mode}


@app.post("/auth/login")
def login(body: schemas.LoginIn, base: Base, response: Response) -> schemas.Me:
    if base.mode == "demo":
        raise ReadOnlyError("demo mode has no accounts")
    email = body.email.lower()
    if throttle.blocked(email):
        raise HTTPException(429, "too many failed logins, try again later")
    found = base.login_user(email)
    stored = found[1] if found else None
    if not auth.verify_password(body.password, stored or auth.DUMMY_HASH) or not stored:
        throttle.failed(email)
        raise HTTPException(401, "wrong e-mail or password")
    throttle.succeeded(email)
    user = found[0]
    token = auth.new_token()
    base.create_session(user.user_id, auth.token_hash(token), auth.SESSION_SECONDS)
    response.set_cookie(
        auth.COOKIE,
        token,
        max_age=auth.SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=get_settings().cookie_secure,
    )
    return schemas.Me.of(user)


@app.post("/auth/logout", status_code=204)
def logout(base: Base, response: Response, session: SessionCookie = None) -> None:
    if session and base.mode != "demo":
        base.delete_session(auth.token_hash(session))
    response.delete_cookie(auth.COOKIE, httponly=True, samesite="lax")


@app.get("/auth/me")
def me(user: Viewer) -> schemas.Me:
    return schemas.Me.of(user)


@app.get("/users")
def users(user: Admin, repo: Repo) -> list[schemas.UserOut]:
    return repo.users()


@app.post("/users", status_code=201)
def create_user(body: schemas.UserIn, user: Admin, repo: Repo) -> schemas.Created:
    # Tenant admins add users to their own tenant only; platform admins choose.
    tenant_id = body.tenant_id if user.is_platform else user.tenant_id
    user_id = repo.create_user(
        tenant_id, body.email.lower(), body.name, body.role, auth.hash_password(body.password)
    )
    return schemas.Created(id=user_id)


@app.get("/buildings")
def buildings(repo: Repo) -> list[schemas.Building]:
    # A meter without its own tariff uses the default, so clients never guess one.
    default = get_settings().tariff_tl_per_kwh
    rows = [dict(b) for b in repo.buildings()]
    for b in rows:
        if b["tariff_tl_per_kwh"] is None:
            b["tariff_tl_per_kwh"] = default
    return rows


@app.get("/buildings/{building_id}/readings")
def readings(
    building_id: BuildingId, repo: Repo, hours: Hours = 48, end: End = None
) -> list[schemas.Reading]:
    return repo.readings(building_id, hours, end)


@app.get("/buildings/{building_id}/forecast")
def forecast(
    building_id: BuildingId, repo: Repo, hours: Hours = 48, end: End = None
) -> list[schemas.ForecastPoint]:
    return repo.forecast(building_id, hours, end)


@app.get("/anomalies")
def anomalies(
    repo: Repo,
    building_id: Annotated[int | None, Query(ge=0, le=schemas.INT_MAX)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[schemas.Anomaly]:
    return repo.anomalies(building_id, limit)


@app.get("/summary")
def summary(repo: Repo) -> schemas.Summary:
    return repo.summary()


@app.get("/tenants")
def tenants(repo: Repo) -> list[schemas.TenantOut]:
    return repo.tenants()


@app.post("/tenants", status_code=201, dependencies=[Depends(require_platform_admin)])
def register_tenant(body: schemas.TenantIn, repo: Repo) -> schemas.Created:
    return schemas.Created(id=repo.register_tenant(TenantInfo(**body.model_dump())))


@app.post("/buildings", status_code=201)
def register(body: schemas.BuildingIn, user: Admin, repo: Repo) -> schemas.Created:
    fields = body.model_dump()
    if not user.is_platform:
        fields["tenant_id"] = user.tenant_id
    return schemas.Created(id=repo.register(BuildingInfo(**fields)))


@app.get("/buildings/{building_id}/operating-hours")
def operating_hours(building_id: BuildingId, repo: Repo) -> list[schemas.OperatingHours]:
    return repo.operating_hours(building_id)


@app.put("/buildings/{building_id}/operating-hours", dependencies=Writer)
def set_operating_hours(
    building_id: BuildingId, body: schemas.Schedule, repo: Repo
) -> list[schemas.OperatingHours]:
    return repo.set_operating_hours(building_id, body.model_dump())


@app.get("/buildings/{building_id}/downtime")
def downtime(building_id: BuildingId, repo: Repo) -> list[schemas.Downtime]:
    return repo.downtime(building_id)


@app.post("/buildings/{building_id}/downtime", status_code=201, dependencies=Writer)
def add_downtime(building_id: BuildingId, body: schemas.DowntimeIn, repo: Repo) -> schemas.Created:
    return schemas.Created(id=repo.add_downtime(building_id, body.model_dump()))


@app.delete("/buildings/{building_id}/downtime/{downtime_id}", status_code=204, dependencies=Writer)
def delete_downtime(building_id: BuildingId, downtime_id: RowId, repo: Repo) -> None:
    repo.delete_downtime(building_id, downtime_id)


@app.post("/buildings/{building_id}/readings/import", dependencies=Writer)
def import_readings(
    building_id: BuildingId,
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


@app.post("/anomalies/{anomaly_id}/feedback", status_code=201)
def feedback(
    anomaly_id: RowId, body: schemas.FeedbackIn, user: Operator, repo: Repo
) -> schemas.Created:
    return schemas.Created(id=repo.add_feedback(anomaly_id, body.verdict, body.note, user.email))
