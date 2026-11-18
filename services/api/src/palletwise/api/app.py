"""FastAPI BFF. Slice 0: health and site status only; no application features."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel

from palletwise.db import status_repo
from palletwise.db.connection import close_pools, role_pool
from palletwise.settings import DEMO_SITE_ID, DEMO_TENANT_ID


class Health(BaseModel):
    ready: bool
    database: bool


class SiteStatusOut(BaseModel):
    code: str
    name: str
    data_status: str
    sim_time: datetime | None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    yield
    close_pools()


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
        with role_pool("PW_APP").connection(timeout=2) as conn:
            conn.execute("SELECT 1")
        return Health(ready=True, database=True)
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(ready=False, database=False)


@app.get(
    "/api/sites/current/status",
    response_model=SiteStatusOut,
    responses={404: {"description": "The site has not been created yet"}},
)
def current_site_status() -> SiteStatusOut:
    with role_pool("PW_APP").connection() as conn:
        site = status_repo.site_status(conn, DEMO_TENANT_ID, DEMO_SITE_ID)
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site not created yet")
    return SiteStatusOut(code=site.code, name=site.name, data_status=site.data_status, sim_time=site.sim_time)
