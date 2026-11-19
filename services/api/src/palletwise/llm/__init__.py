"""LLMProvider interface and adapters (agent spec 5). Nothing else imports a model SDK."""

import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Protocol

import yaml

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")  # no start-up banner in server logs

from pydantic_ai.models import Model

from palletwise.settings import LlmSettings

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


class VertexGeminiProvider:
    def __init__(self, settings: LlmSettings) -> None:
        from google import genai
        from google.genai.types import HttpOptions
        from google.oauth2 import service_account
        from pydantic_ai.providers.google import GoogleProvider

        assert settings.project and settings.credentials_file
        credentials = service_account.Credentials.from_service_account_file(  # pyright: ignore[reportUnknownMemberType]
            settings.credentials_file, scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        client = genai.Client(
            vertexai=True,
            project=settings.project,
            location=settings.location,
            credentials=credentials,
            http_options=HttpOptions(timeout=int(settings.request_timeout_seconds * 1000)),
        )
        self._provider = GoogleProvider(client=client)
        self._settings = settings

    def model(self, logical: str) -> ResolvedModel | None:
        from pydantic_ai.models.google import GoogleModel

        name = self._settings.models.get(logical)
        if name is None:
            log.warning("no provider model configured for %s", logical)
            return None
        return ResolvedModel(GoogleModel(name, provider=self._provider), name, self._settings.location)


def build_provider(settings: LlmSettings) -> LLMProvider:
    if not settings.enabled:
        return DisabledProvider()
    if not settings.project or not settings.credentials_file or not Path(settings.credentials_file).is_file():
        log.info("model credentials not configured; the copilot answers on path B only")
        return DisabledProvider()
    return VertexGeminiProvider(settings)


@dataclass(frozen=True, slots=True)
class Price:
    input: Decimal
    output: Decimal
    cached: Decimal


@cache
def prices() -> dict[str, Price]:
    raw = yaml.safe_load(PRICES_FILE.read_text(encoding="utf-8"))["models"]
    return {name: Price(Decimal(str(p["input"])), Decimal(str(p["output"])), Decimal(str(p["cached"])))
            for name, p in raw.items()}  # fmt: skip


def estimated_cost(model: str, input_tokens: int, output_tokens: int, cached_tokens: int) -> Decimal:
    """Provider-reported tokens times the price table (evals spec 14). Unknown model: 0."""
    price = prices().get(model)
    if price is None:
        return Decimal(0)
    billed_input = max(input_tokens - cached_tokens, 0)
    cost = (
        billed_input * price.input + output_tokens * price.output + cached_tokens * price.cached
    ) / PER_MILLION
    return cost.quantize(Decimal("0.000001"))
