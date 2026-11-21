"""LLMProvider interface and adapters (agent spec 5). Nothing else imports a model SDK."""

import logging
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import yaml

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")  # no start-up banner in server logs

from pydantic_ai.models import Model

from palletwise.settings import LlmSettings

if TYPE_CHECKING:
    from google import genai

log = logging.getLogger(__name__)

PRICES_FILE = Path(__file__).with_name("model_prices.yaml")
PER_MILLION = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    model: Model
    name: str  # the provider model id, for usage records
    region: str


class LLMProvider(Protocol):
    """Gives a model for a logical name, or None when it is unavailable or disabled."""

    def model(self, logical: str) -> ResolvedModel | None: ...


class DisabledProvider:
    """No model at all: the copilot answers on path B only."""

    def model(self, logical: str) -> ResolvedModel | None:
        return None


class GeminiProvider:
    """Gemini models through one Google GenAI client: Vertex AI or the Gemini API."""

    def __init__(self, client: "genai.Client", settings: LlmSettings) -> None:
        from pydantic_ai.providers.google import GoogleProvider

        self._provider = GoogleProvider(client=client)
        self._settings = settings

    def model(self, logical: str) -> ResolvedModel | None:
        from pydantic_ai.models.google import GoogleModel

        name = self._settings.models.get(logical)
        if name is None:
            log.warning("no provider model configured for %s", logical)
            return None
        return ResolvedModel(GoogleModel(name, provider=self._provider), name, self._settings.location)


def _vertex_client(settings: LlmSettings) -> "genai.Client":
    """Vertex AI with a service account file (kept outside the repository, mounted read-only)."""
    from google import genai
    from google.genai.types import HttpOptions
    from google.oauth2 import service_account

    assert settings.project and settings.credentials_file
    credentials = service_account.Credentials.from_service_account_file(  # pyright: ignore[reportUnknownMemberType]
        settings.credentials_file, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return genai.Client(
        vertexai=True,
        project=settings.project,
        location=settings.location,
        credentials=credentials,
        http_options=HttpOptions(timeout=int(settings.request_timeout_seconds * 1000)),
    )


def _api_key_client(settings: LlmSettings) -> "genai.Client":
    """The Gemini API with an API key from Google AI Studio (the same models)."""
    from google import genai
    from google.genai.types import HttpOptions

    assert settings.api_key is not None
    return genai.Client(
        api_key=settings.api_key.get_secret_value(),
        http_options=HttpOptions(timeout=int(settings.request_timeout_seconds * 1000)),
    )


def build_provider(settings: LlmSettings) -> LLMProvider:
    """Vertex AI when a service account is configured, else a Gemini API key, else no model."""
    if not settings.enabled:
        return DisabledProvider()
    if settings.project and settings.credentials_file and Path(settings.credentials_file).is_file():
        log.info("model provider: Gemini on Vertex AI")
        return GeminiProvider(_vertex_client(settings), settings)
    if settings.api_key is not None and settings.api_key.get_secret_value().strip():
        log.info("model provider: Gemini API with an API key")
        return GeminiProvider(_api_key_client(settings), settings)
    log.info("model credentials not configured; the copilot answers on path B only")
    return DisabledProvider()


@dataclass(frozen=True, slots=True)
class Price:
    effective_from: date
    input: Decimal
    output: Decimal
    cached: Decimal


@dataclass(frozen=True, slots=True)
class PriceTable:
    provider: str
    currency: str
    models: dict[str, tuple[Price, ...]]  # each sorted by effective_from

    def price(self, model: str, on: date) -> Price | None:
        """The period in force on the given date, or None for an unknown model or date."""
        current = None
        for period in self.models.get(model, ()):
            if period.effective_from <= on:
                current = period
        return current


@cache
def prices() -> PriceTable:
    raw = yaml.safe_load(PRICES_FILE.read_text(encoding="utf-8"))
    models = {
        name: tuple(sorted(
            (Price(p["effective_from"], Decimal(str(p["input"])), Decimal(str(p["output"])),
                   Decimal(str(p["cached"]))) for p in periods),
            key=lambda price: price.effective_from,
        ))
        for name, periods in raw["models"].items()
    }  # fmt: skip
    return PriceTable(raw["provider"], raw["currency"], models)


def estimated_cost(
    model: str, input_tokens: int, output_tokens: int, cached_tokens: int, on: date
) -> Decimal:
    """Provider-reported tokens times the price in force on the call's date (evals spec 14).

    Unknown model: 0, and the usage record still keeps the tokens. Cached input is billed at the
    cached rate instead of the input rate; output already includes thinking tokens.
    """
    price = prices().price(model, on)
    if price is None:
        log.warning("no price for model %s on %s; estimated cost recorded as 0", model, on)
        return Decimal(0)
    billed_input = max(input_tokens - cached_tokens, 0)
    cost = (
        billed_input * price.input + output_tokens * price.output + cached_tokens * price.cached
    ) / PER_MILLION
    return cost.quantize(Decimal("0.000001"))
