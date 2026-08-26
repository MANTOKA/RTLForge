from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="run tests that build and execute the Docker EDA sandbox",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-integration"):
        return

    skip_integration = pytest.mark.skip(reason="use --run-integration to run Docker tests")
    for item in items:
        if item.get_closest_marker("integration") is not None:
            item.add_marker(skip_integration)

