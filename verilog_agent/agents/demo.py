"""Backward-compatible entry point for the relocated single-case demo."""

from verilog_agent.benchmarks.single_case_demo import (
    _load_provider_environment,
    _print_provider_config,
    main,
)

__all__ = ["main", "_load_provider_environment", "_print_provider_config"]


if __name__ == "__main__":
    main()
