"""Process configuration from the environment.

Each process reads only the database credentials of its own role
(engineering baseline 3: a common codebase is not a common identity).
"""

from uuid import UUID

from pydantic import AliasChoices, Field
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


class OidcSettings(BaseSettings):
    """The API as an OIDC client of Keycloak (identity spec 2).

    The browser reaches Keycloak at the public URL; the API calls it at the internal URL.
    Both serve the same realm, and tokens carry the public URL as their issuer.
    """

    model_config = SettingsConfigDict(env_prefix="PW_OIDC_", extra="ignore")

    public_url: str = "http://localhost:8180/realms/palletwise"
    internal_url: str = "http://keycloak:8080/realms/palletwise"
    client_id: str = "palletwise-bff"
    client_secret: str = Field(repr=False)
    http_timeout_seconds: float = 5.0


class SessionSettings(BaseSettings):
    """Browser sessions (identity spec 3)."""

    model_config = SettingsConfigDict(env_prefix="PW_SESSION_", extra="ignore")

    # Comma-separated "key_id:base64 32-byte key" pairs; the first encrypts, all decrypt.
    keys: str = Field(repr=False)
    idle_minutes: int = 30
    absolute_hours: int = 12
    # Matches the Keycloak access token lifetime, so revocation takes effect within it.
    revalidate_seconds: int = 300
    login_ttl_minutes: int = 10


class WebSettings(BaseSettings):
    """Where the browser reaches PalletWise; used for redirects and the Origin check."""

    model_config = SettingsConfigDict(env_prefix="PW_", extra="ignore")

    public_origin: str = "http://localhost:8080"


class PolicySettings(BaseSettings):
    """The OPA decision endpoint (identity spec 6). Unreachable means deny."""

    model_config = SettingsConfigDict(env_prefix="PW_OPA_", extra="ignore")

    url: str = "http://opa:8181"
    timeout_seconds: float = 2.0


class LlmSettings(BaseSettings):
    """Gemini on Vertex AI (agent spec 5). Without credentials the copilot runs on path B."""

    model_config = SettingsConfigDict(env_prefix="PW_LLM_", extra="ignore")

    enabled: bool = True
    project: str | None = None
    # Newer Gemini models are served from the global endpoint only.
    location: str = "global"
    credentials_file: str | None = None
    # Logical names (agent registry) to provider model ids.
    models: dict[str, str] = {
        "gemini-flash": "gemini-3.8-flash",
        "gemini-flash-lite": "gemini-3.5-flash-lite",
        "gemini-flash-fallback": "gemini-3.5-flash",
    }
    request_timeout_seconds: float = 15.0
    run_timeout_seconds: float = 45.0


class TelemetrySettings(BaseSettings):
    """Traces, metrics and logs (evals spec 9 to 13). Export is off unless an endpoint is set."""

    model_config = SettingsConfigDict(env_prefix="PW_TELEMETRY_", extra="ignore", populate_by_name=True)

    # "production" refuses content capture at startup (evals spec 11).
    environment: str = "development"
    # Prompts and responses as span events: development only, on synthetic data.
    trace_content: bool = Field(default=False, validation_alias="PW_TRACE_CONTENT")
    # Salt for masking personal data in exported telemetry. A secret; required in production.
    mask_salt: str | None = None
    # Share of non-copilot root traces kept; copilot runs are always kept.
    sample_ratio: float = Field(default=0.1, ge=0.0, le=1.0)
    # Any OTLP/HTTP traces endpoint, for example a collector.
    otlp_endpoint: str | None = None
    # Langfuse via its OTLP endpoint, when both keys are set.
    langfuse_host: str | None = Field(
        default=None, validation_alias=AliasChoices("LANGFUSE_BASE_URL", "LANGFUSE_HOST")
    )
    langfuse_public_key: str | None = Field(default=None, validation_alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str | None = Field(default=None, validation_alias="LANGFUSE_SECRET_KEY")
    # The API serves /metrics here, on an internal port the edge does not route.
    metrics_port: int = 9464
