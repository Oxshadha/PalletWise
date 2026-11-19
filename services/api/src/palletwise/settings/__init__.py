"""Process configuration from the environment.

Each process reads only the database credentials of its own role
(engineering baseline 3: a common codebase is not a common identity).
"""

from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Connection settings shared by every process; the role is per process."""

    model_config = SettingsConfigDict(env_prefix="PW_DB_", extra="ignore")

    host: str = "localhost"
    port: int = 5432
    name: str = "palletwise"


class RoleCredentials(BaseSettings):
    """Credentials for one database role, read from PW_<ROLE>_USER / _PASSWORD."""

    model_config = SettingsConfigDict(extra="ignore")

    user: str
    password: str = Field(repr=False)


def role_credentials(role_env_prefix: str) -> RoleCredentials:
    """Load credentials for a role, e.g. role_credentials("PW_INGEST")."""

    class _Creds(RoleCredentials):
        model_config = SettingsConfigDict(env_prefix=f"{role_env_prefix}_", extra="ignore")

    return _Creds()  # pyright: ignore[reportCallIssue]


class SiteSettings(BaseSettings):
    """The simulated v1 site served by this deployment."""

    model_config = SettingsConfigDict(env_prefix="PW_SITE_", extra="ignore")

    tenant_code: str = "demo"
    tenant_name: str = "Demo Distribution"
    code: str = "PLG"
    name: str = "Peliyagoda Distribution Centre"
    region_code: str = "LK"
    time_zone: str = "Asia/Colombo"


class SimulationSettings(BaseSettings):
    """How the simulation admin service starts a run."""

    model_config = SettingsConfigDict(env_prefix="PW_SIM_", extra="ignore")

    seed: int = 42
    dataset_version: str = "sim-v1"
    control_url: str = "http://simulator:8000"
    golden_dir: str = "services/simulator/golden"
    # Simulator spec 5.1: history of 180 days before 2026-04-01 06:00 Asia/Colombo.
    clock_start: str = "2026-04-01T06:00:00+05:30"
    history_days: int = 180


# Fixed identifiers of the v1 demo deployment. Deterministic so that every
# component and test refers to the same tenant and site without discovery.
DEMO_TENANT_ID = UUID("0193a000-0000-7000-8000-000000000001")
DEMO_SITE_ID = UUID("0193a000-0000-7000-8000-000000000002")
DEMO_CONNECTOR_ID = UUID("0193a000-0000-7000-8000-000000000003")
