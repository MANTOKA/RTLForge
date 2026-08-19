from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
IMAGE = "verilog-agent-sandbox:test"


def _docker_binary() -> str:
    configured = os.environ.get("DOCKER_BIN")
    if configured:
        return configured

    discovered = shutil.which("docker")
    if discovered:
        return discovered

    pytest.fail("Docker CLI was not found; set DOCKER_BIN to its executable path")


def _run(command: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


@pytest.fixture(scope="session")
def docker_image() -> str:
    docker = _docker_binary()
    result = _run([docker, "build", "-t", IMAGE, str(REPO_ROOT)], timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    return IMAGE


def _sandbox_command(docker_image: str) -> list[str]:
    docker = _docker_binary()
    mount = f"type=bind,source={REPO_ROOT},target=/repo,readonly"
    return [
        docker,
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/work:rw,exec,size=64m,uid=10001,gid=10001,mode=0700",
        "--workdir",
        "/work",
        "--env",
        "TMPDIR=/work",
        "--env",
        "VERILOG_AGENT_INPUT_ROOT=/repo/tests/fixtures",
        "--pids-limit",
        "128",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--mount",
        mount,
        docker_image,
    ]


def _wrapper(
    docker_image: str,
    action: str,
    *fixture_names: str,
    environment: dict[str, str] | None = None,
) -> dict:
    command = _sandbox_command(docker_image)
    for key, value in (environment or {}).items():
        command[command.index(docker_image):command.index(docker_image)] = [
            "--env",
            f"{key}={value}",
        ]
    input_paths = [
        name if name.startswith("/") else f"/repo/tests/fixtures/{name}"
        for name in fixture_names
    ]
    command.extend(
        [
        "python3",
        "-u",
        "/repo/tool_wrapper.py",
        action,
        *input_paths,
        ]
    )
    result = _run(command, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.integration
def test_valid_counter_compiles(docker_image: str) -> None:
    result = _wrapper(docker_image, "compile", "counter.v")

    assert result["status"] == "PASS"
    assert result["errors"] == []


@pytest.mark.integration
def test_invalid_counter_reports_line_seven(docker_image: str) -> None:
    result = _wrapper(docker_image, "compile", "counter_bad.v")

    assert result["status"] == "FAIL"
    assert result["errors"][0]["line"] == 7
    assert result["errors"][0]["message"] == "syntax error"


@pytest.mark.integration
def test_valid_counter_passes_lint(docker_image: str) -> None:
    result = _wrapper(docker_image, "lint", "counter.v")

    assert result["status"] == "PASS"


@pytest.mark.integration
def test_counter_testbench_simulates(docker_image: str) -> None:
    result = _wrapper(docker_image, "simulate", "tb_counter.v", "counter.v")

    assert result["status"] == "PASS"
    assert "Simulation finished" in result["stdout"]


@pytest.mark.integration
def test_wrapper_does_not_write_build_products_to_repository(docker_image: str) -> None:
    _wrapper(docker_image, "simulate", "tb_counter.v", "counter.v")

    assert not (REPO_ROOT / "a.out").exists()
    assert not (REPO_ROOT / "sim.out").exists()


@pytest.mark.integration
def test_sandbox_runs_non_root_with_complete_toolchain(docker_image: str) -> None:
    command = _sandbox_command(docker_image)
    command.extend(
        [
            "sh",
            "-c",
            "test \"$(id -u)\" -ne 0"
            " && command -v iverilog"
            " && command -v verilator"
            " && command -v yosys",
        ]
    )

    result = _run(command)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_input_outside_allowed_root_is_rejected(docker_image: str) -> None:
    result = _wrapper(
        docker_image,
        "compile",
        "counter.v",
        environment={"VERILOG_AGENT_INPUT_ROOT": "/repo/tests/unit"},
    )

    assert result["status"] == "FAIL"
    assert result["category"] == "PATH_VIOLATION"


@pytest.mark.integration
def test_infinite_simulation_times_out(docker_image: str) -> None:
    result = _wrapper(
        docker_image,
        "simulate",
        "tb_timeout.v",
        environment={"VERILOG_AGENT_SIMULATE_TIMEOUT": "1"},
    )

    assert result["status"] == "FAIL"
    assert result["category"] == "TIMEOUT"
