"""Which model provider runs: Vertex AI first, then a Gemini API key, else none (path B)."""

import importlib.util
from pathlib import Path

import pytest
from pydantic import SecretStr

from palletwise.llm import DisabledProvider, GeminiProvider, build_provider
from palletwise.settings import LlmSettings


def test_no_credentials_means_no_model() -> None:
    assert isinstance(build_provider(LlmSettings(project=None, credentials_file=None)), DisabledProvider)


def test_a_blank_api_key_counts_as_none() -> None:
    settings = LlmSettings(project=None, credentials_file=None, api_key=SecretStr("  "))
    assert isinstance(build_provider(settings), DisabledProvider)


def test_a_gemini_api_key_gives_the_same_models() -> None:
    settings = LlmSettings(project=None, credentials_file=None, api_key=SecretStr("AIza-test-key"))
    provider = build_provider(settings)
    assert isinstance(provider, GeminiProvider)
    resolved = provider.model("gemini-flash-lite")
    assert resolved is not None and resolved.name == settings.models["gemini-flash-lite"]


def test_the_key_never_shows_in_the_settings_repr() -> None:
    settings = LlmSettings(api_key=SecretStr("AIza-secret-value"))
    assert "AIza-secret-value" not in repr(settings) and "AIza-secret-value" not in str(settings)


def test_setup_writes_the_key_over_its_example(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location("setup_script", Path("scripts/setup.py"))
    assert spec and spec.loader
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    env = tmp_path / ".env"
    env.write_text("A=1\n# GEMINI_API_KEY=\nB=2\n", encoding="utf-8")
    monkeypatch.setattr(setup, "ENV", env)
    setup.set_env("GEMINI_API_KEY", "AIza-one")
    setup.set_env("GEMINI_API_KEY", "AIza-two")
    setup.set_env("OTHER", "x")
    assert env.read_text(encoding="utf-8") == "A=1\nGEMINI_API_KEY=AIza-two\nB=2\nOTHER=x\n"
