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
    existing_image = os.environ.get("VERILOG_AGENT_TEST_IMAGE")
    if existing_image:
        return existing_image
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
def test_verified_human_v2_candidate_compiles(docker_image: str) -> None:
    result = _wrapper(docker_image, "compile", "human_v2_prob035/TopModule.v")

    assert result["status"] == "PASS"
    assert result["errors"] == []
    assert {
        "schema_version",
        "action",
        "stage",
        "status",
        "category",
        "tool",
        "returncode",
        "duration_ms",
        "errors",
        "metrics",
        "stdout",
        "stderr",
    } <= result.keys()


@pytest.mark.integration
def test_real_rejected_candidate_reports_procedural_wire_error(
    docker_image: str,
) -> None:
    result = _wrapper(docker_image, "compile", "human_v2_prob094_invalid/TopModule.v")

    assert result["status"] == "FAIL"
    assert result["category"] == "SYNTAX_ERROR"
    assert result["errors"][0]["line"] == 12
    assert "not a valid l-value" in result["errors"][0]["message"]


@pytest.mark.integration
def test_verified_human_v2_candidate_passes_lint(docker_image: str) -> None:
    result = _wrapper(docker_image, "lint", "human_v2_prob035/TopModule.v")

    assert result["status"] == "PASS"


@pytest.mark.integration
def test_human_v2_counter_testbench_simulates(docker_image: str) -> None:
    result = _wrapper(
        docker_image,
        "simulate",
        "human_v2_prob035/tb.v",
        "human_v2_prob035/TopModule.v",
    )

    assert result["status"] == "PASS"
    assert "Simulation finished" in result["stdout"]


@pytest.mark.integration
def test_prob104_first_sample_initialization_repair(docker_image: str) -> None:
    result = _wrapper(
        docker_image,
        "simulate",
        "human_v2_prob104/tb.v",
        "human_v2_prob104/TopModule.v",
    )

    assert result["status"] == "PASS"
    assert "Simulation finished" in result["stdout"]


@pytest.mark.integration
def test_wrapper_does_not_write_build_products_to_repository(docker_image: str) -> None:
    _wrapper(
        docker_image,
        "simulate",
        "human_v2_prob035/tb.v",
        "human_v2_prob035/TopModule.v",
    )

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
        "human_v2_prob035/TopModule.v",
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


@pytest.mark.integration
def test_zero_exit_fail_text_is_not_treated_as_pass(docker_image: str) -> None:
    result = _wrapper(docker_image, "simulate", "tb_fail_text.v")

    assert result["status"] == "FAIL"
    assert result["category"] == "ASSERTION_FAILURE"


@pytest.mark.integration
def test_zero_exit_nonzero_mismatch_count_is_not_treated_as_pass(
    docker_image: str,
) -> None:
    result = _wrapper(docker_image, "simulate", "tb_mismatch_count.v")

    assert result["status"] == "FAIL"
    assert result["category"] == "ASSERTION_FAILURE"


@pytest.mark.integration
def test_quality_evaluation_returns_deterministic_metrics(docker_image: str) -> None:
    result = _wrapper(docker_image, "eval_quality", "human_v2_prob035/TopModule.v")

    assert result["status"] == "PASS"
    assert result["metrics"] == {
        "score": 100,
        "threshold": 75,
        "config_version": "score-v1",
        "error_count": 0,
        "warning_count": 0,
        "deductions": [],
    }


@pytest.mark.integration
def test_yosys_benchmark_returns_structural_metrics(docker_image: str) -> None:
    result = _wrapper(docker_image, "benchmark", "human_v2_prob035/TopModule.v")

    assert result["status"] == "PASS"
    assert result["tool"] == "yosys"
    assert result["metrics"]["wire_count"] > 0
    assert result["metrics"]["wire_bits"] > 0
    assert result["metrics"]["cell_count"] > 0
    assert result["metrics"]["scope"] == "structural_only_no_sta"
