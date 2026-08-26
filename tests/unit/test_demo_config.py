from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from verilog_agent.agents.demo import _load_provider_environment, _print_provider_config
from verilog_agent.providers import ProviderSettings


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser()


def test_demo_loads_private_env_file_without_printing_or_returning_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=private-secret\n"
        "VERILOG_AGENT_MODEL=relay-model\n"
        "OPENAI_BASE_URL=https://relay.example/v1\n",
        encoding="utf-8",
    )
    for name in ("OPENAI_API_KEY", "VERILOG_AGENT_MODEL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    loaded_path = _load_provider_environment(_parser(), env_file)
    settings = ProviderSettings.from_env()

    assert loaded_path == env_file.resolve()
    assert settings.model == "relay-model"
    assert settings.base_url == "https://relay.example/v1"
    assert "private-secret" not in repr(settings)
    assert "private-secret" not in str(loaded_path)


def test_shell_environment_takes_precedence_over_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=file-key\nVERILOG_AGENT_MODEL=file-model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "shell-key")
    monkeypatch.setenv("VERILOG_AGENT_MODEL", "shell-model")

    _load_provider_environment(_parser(), env_file)

    settings = ProviderSettings.from_env()
    assert settings.api_key == "shell-key"
    assert settings.model == "shell-model"


def test_default_missing_env_file_is_optional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert _load_provider_environment(_parser(), None) is None


def test_explicit_missing_env_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        _load_provider_environment(_parser(), tmp_path / "missing.env")


def test_config_summary_hides_api_key(capsys: pytest.CaptureFixture[str]) -> None:
    settings = ProviderSettings(
        api_key="never-print-this",
        model="relay-model",
        base_url="https://relay.example/v1",
    )

    _print_provider_config(settings, Path("C:/private/.env"))

    output = capsys.readouterr().out
    assert "never-print-this" not in output
    assert "relay-model" in output
    assert "https://relay.example/v1/responses" in output
    assert "NO API CALL" in output
