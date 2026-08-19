from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_default_configuration_is_versioned_and_safe() -> None:
    config_path = REPO_ROOT / "config" / "default.toml"
    with config_path.open("rb") as config_file:
        config = tomllib.load(config_file)

    assert config["schema_version"] == 1
    assert config["sandbox"]["network"] == "none"
    assert config["sandbox"]["read_only_root"] is True
    assert config["sandbox"]["user_uid"] != 0
    assert config["logging"]["format"] == "jsonl"
    assert "api_key" in config["logging"]["redact_keys"]


def test_github_contribution_templates_are_present() -> None:
    expected = [
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/pull_request_template.md",
    ]

    assert all((REPO_ROOT / path).is_file() for path in expected)
