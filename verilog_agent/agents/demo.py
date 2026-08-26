from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from verilog_agent.agents.showcase import PlannerGeneratorShowcase, ShowcaseRunError
from verilog_agent.providers import (
    OpenAIResponsesProvider,
    ProviderConfigurationError,
    ProviderSettings,
)
from verilog_agent.testing import ScriptedProvider
from verilog_agent.testing.examples import (
    OFFLINE_COUNTER_SPEC,
    counter_candidate_payload,
    counter_plan_payload,
)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=(
            "Show Planner SpecIR/Task Tree and Generator RTL candidates step by step. "
            "Live mode consumes model API tokens."
        )
    )
    parser.add_argument(
        "spec",
        nargs="?",
        help="Natural-language RTL requirement; omit it to enter the requirement interactively",
    )
    parser.add_argument("--offline-example", action="store_true")
    parser.add_argument("--database", type=Path, default=Path("work/showcase.sqlite"))
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument("--candidates", type=int, choices=(2, 3), default=2)
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Local env file for live mode; defaults to .env in the current directory",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate and display non-secret live provider settings without making an API call",
    )
    args = parser.parse_args()

    if args.offline_example:
        if args.env_file is not None or args.check_config:
            parser.error("--env-file and --check-config are only available in live mode")
        if args.spec is not None:
            parser.error("--offline-example uses its built-in counter requirement; omit spec")
        if args.candidates != 2:
            parser.error("--offline-example currently contains exactly two scripted candidates")
        raw_spec = OFFLINE_COUNTER_SPEC
        provider = ScriptedProvider(counter_plan_payload(), counter_candidate_payload())
        provider_label = "scripted_offline_example (NO LLM API CALL)"
    else:
        loaded_env_file = _load_provider_environment(parser, args.env_file)
        try:
            settings = ProviderSettings.from_env()
            provider = OpenAIResponsesProvider(settings)
        except ProviderConfigurationError as error:
            parser.error(str(error))
        if args.check_config:
            _print_provider_config(settings, loaded_env_file)
            return
        raw_spec = args.spec or _prompt_for_spec(parser)
        if not raw_spec.strip():
            parser.error("RTL requirement must not be empty")
        endpoint = settings.base_url or "https://api.openai.com/v1"
        config_source = f" env_file={loaded_env_file}" if loaded_env_file else ""
        provider_label = (
            f"openai_responses model={settings.model} endpoint={endpoint}{config_source}"
        )

    showcase = PlannerGeneratorShowcase(
        provider=provider,
        provider_label=provider_label,
        database=args.database,
        runs_root=args.runs_root,
    )
    try:
        showcase.run(raw_spec, candidate_count=args.candidates)
    except ShowcaseRunError as error:
        parser.exit(1, f"showcase error: {error}\n")


def _prompt_for_spec(parser: argparse.ArgumentParser) -> str:
    print("请输入一行 RTL 自然语言需求，然后按 Enter：")
    raw_spec = input("> ").strip()
    if not raw_spec:
        parser.error("RTL requirement must not be empty")
    return raw_spec


def _load_provider_environment(
    parser: argparse.ArgumentParser, requested_path: Path | None
) -> Path | None:
    """Load a private env file without overriding values already set by the shell."""

    env_path = requested_path or Path(".env")
    if not env_path.is_file():
        if requested_path is not None:
            parser.error(f"env file does not exist: {env_path}")
        return None
    try:
        load_dotenv(dotenv_path=env_path, override=False)
    except (OSError, UnicodeError) as error:
        parser.error(f"cannot read env file {env_path}: {error}")
    return env_path.resolve()


def _print_provider_config(settings: ProviderSettings, env_file: Path | None) -> None:
    endpoint = settings.base_url or "https://api.openai.com/v1"
    print("Provider configuration OK (NO API CALL)")
    print("provider: openai_responses")
    print(f"model: {settings.model}")
    print(f"base_url: {endpoint}")
    print(f"env_file: {env_file or 'not used; values came from the shell'}")
    print("api_key: configured (value hidden)")
    print(f"required endpoint: POST {endpoint.rstrip('/')}/responses")


if __name__ == "__main__":
    main()
