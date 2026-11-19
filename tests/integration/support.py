"""Helpers shared by integration tests."""

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg

from palletwise.db import site_repo, status_repo

CLOCK_START = datetime(2026, 4, 1, 6, 0, tzinfo=ZoneInfo("Asia/Colombo"))


def provision_site(
    connect_as: Callable[[str], psycopg.Connection], tenant: UUID | None = None, site: UUID | None = None
) -> tuple[UUID, UUID]:
    """Creates a tenant and an empty site through the simulation admin path (idempotent per id)."""
    tenant, site = tenant or uuid4(), site or uuid4()
    admin = connect_as("pw_sim_admin")
    if status_repo.site_status(admin, tenant, site) is None:
        site_repo.bootstrap_site(
            admin,
            site_repo.SiteBootstrap(
                tenant_id=tenant, tenant_code=f"t-{tenant.hex[:8]}", tenant_name="Test",
                site_id=site, site_code="PLG", site_name="Test site", region_code="LK",
                time_zone="Asia/Colombo", width_m=80.0, depth_m=50.0, connector_id=uuid4(),
                run_id=uuid4(), seed=42, dataset_version="test",
                history_start=CLOCK_START - timedelta(days=1), clock_start=CLOCK_START,
                started_by="test",
            ),
        )  # fmt: skip
    return tenant, site
