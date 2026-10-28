from __future__ import annotations

import subprocess
from pathlib import Path

import tool_wrapper


def test_validate_input_rejects_path_escape(tmp_path: Path, monkeypatch) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.v"
    outside.write_text("module outside; endmodule\n", encoding="utf-8")
    monkeypatch.setenv("RTLFORGE_INPUT_ROOT", str(allowed))

    files, error = tool_wrapper.validate_input_files([str(outside)])

    assert files == []
    assert error is not None
    assert error["category"] == "PATH_VIOLATION"


def test_run_command_classifies_timeout(monkeypatch) -> None:
    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=1, output="partial")

    monkeypatch.setattr(tool_wrapper.subprocess, "run", raise_timeout)

    result = tool_wrapper.run_command(["slow-tool"], timeout_seconds=1)

    assert result["timed_out"] is True
    assert result["stdout"] == "partial"


def test_compile_uses_unique_temporary_output(tmp_path: Path, monkeypatch) -> None:
    outputs = []

    def fake_run(cmd, *, timeout_seconds):
        outputs.append(cmd[cmd.index("-o") + 1])
        return {
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "system_error": None,
        }

    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(tool_wrapper, "run_command", fake_run)

    tool_wrapper.compile_verilog(["one.v"])
    tool_wrapper.compile_verilog(["two.v"])

    assert outputs[0] != outputs[1]
    assert all(Path(output).parent.parent == tmp_path for output in outputs)
    assert list(tmp_path.iterdir()) == []
