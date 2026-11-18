"""FastAPI BFF. Slice 0: health and site status only; no application features."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Response, status
from pydantic import BaseModel

from palletwise.db import status_repo
from palletwise.db.connection import connect
from palletwise.settings import DEMO_SITE_ID, DEMO_TENANT_ID, DatabaseSettings, role_credentials


class Health(BaseModel):
    ready: bool
    database: bool


class SiteStatusOut(BaseModel):
    code: str
    name: str
    data_status: str
    sim_time: datetime | None


def _dsn() -> str:
    creds = role_credentials("PW_APP")
    return DatabaseSettings().dsn(creds.user, creds.password)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    yield


app = FastAPI(
    title="PalletWise API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.get("/api/health", response_model=Health)
def health(response: Response) -> Health:
    try:
        with connect(_dsn()) as conn:
            conn.execute("SELECT 1")
        return Health(ready=True, database=True)
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(ready=False, database=False)


@app.get("/api/sites/current/status", response_model=SiteStatusOut)
def current_site_status(response: Response) -> SiteStatusOut | None:
    with connect(_dsn()) as conn:
        site = status_repo.site_status(conn, DEMO_TENANT_ID, DEMO_SITE_ID)
    if site is None:
        response.status_code = status.HTTP_404_NOT_FOUND
        return None
    return SiteStatusOut(code=site.code, name=site.name, data_status=site.data_status, sim_time=site.sim_time)
