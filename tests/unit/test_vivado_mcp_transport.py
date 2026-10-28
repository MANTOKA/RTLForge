"""Protocol integration against a test MCP server; does not claim real Vivado synthesis."""

from __future__ import annotations

import json
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from rtlforge.domain import Candidate, RTLTaskContext
from rtlforge.domain.synthesis import SynthesisConstraints
from rtlforge.services.vivado_mcp import VivadoMCPRunner


@pytest.mark.integration
@pytest.mark.parametrize("with_dependency", [False, True])
def test_stdio_mcp_session_executes_monitors_and_stops(tmp_path, monkeypatch, with_dependency):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client import stdio

    server = tmp_path / "test_mcp_server.py"
    server.write_text('''
from pathlib import Path
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("test-vivado-server")
@mcp.tool()
def vivado_start(working_dir: str, session_type: str, gui_mode: bool,
                 vivado_path: str, external: bool) -> dict:
    return {"session_id": "test-session"}
@mcp.tool()
def vivado_execute(session_id: str, command: str, capture_log: bool) -> dict:
    assert command.startswith('source "')
    Path("metrics.tsv").write_text("vivado_version\\tTEST_ONLY\\nclock_count\\t1\\nwns_ns\\t2.0\\n")
    Path("utilization.rpt").write_text("| Slice LUTs | 12 |\\n| Slice Registers | 8 |\\n")
    Path("timing.rpt").write_text("TEST_ONLY")
    Path("check_timing.rpt").write_text("There are 0 register/latch pins with no clock\\n"
        "5. checking unconstrained_internal_endpoints\\n"
        "There are 0 pins that are not constrained for maximum delay.\\n")
    Path("synth.dcp").write_bytes(b"TEST_ONLY")
    Path("finished.txt").write_text("done")
    return {"status": "completed"}
@mcp.tool()
def vivado_status(session_id: str) -> dict:
    return {"status": "ready"}
@mcp.tool()
def vivado_stop(session_id: str, close_vivado: bool) -> dict:
    Path("stopped.txt").write_text(session_id)
    return {"status": "stopped"}
mcp.run(transport="stdio")
''', encoding="utf-8")
    original = stdio.stdio_client

    def test_transport(params, errlog):
        return original(StdioServerParameters(
            command=sys.executable, args=[str(server)], env=params.env, cwd=params.cwd,
        ), errlog=errlog)

    monkeypatch.setattr(stdio, "stdio_client", test_transport)
    timeouts = {}
    original_call = ClientSession.call_tool

    async def record_timeout(session, name, *args, **kwargs):
        timeouts[name] = kwargs["read_timeout_seconds"].total_seconds()
        return await original_call(session, name, *args, **kwargs)

    monkeypatch.setattr(ClientSession, "call_tool", record_timeout)
    rtl = tmp_path / "counter.sv"
    rtl.write_text("module counter; endmodule")
    candidate = Candidate("cand_01", str(rtl), sha256(rtl.read_bytes()).hexdigest())
    context = RTLTaskContext("task", "counter", candidates=[candidate],
                             synthesis_constraints=SynthesisConstraints(
                                 part="test_part", top="counter", clock_port="clk",
                                 clock_period_ns=10,
                             ))
    if with_dependency:
        from rtlforge.integration import attach_modules
        from tests.unit.test_integration_entry import module_fixture

        _, module = module_fixture(tmp_path / "previous")
        attach_modules(context, tmp_path / "task", [module])
    runner = VivadoMCPRunner(Path(sys.executable), Path(sys.executable), tmp_path)
    result = runner.synthesize(context, candidate)
    assert result.status == "PASS", result.diagnostics
    assert result.mcp_server_info["name"] == "test-vivado-server"
    script = Path(result.evidence["script"]).read_text()
    assert script.count("read_verilog -sv") == (2 if with_dependency else 1)
    assert len(result.evidence["dependencies"]) == int(with_dependency)
    root = Path(result.evidence["directory"])
    assert (root / "stopped.txt").read_text() == "test-session"
    calls = [json.loads(line) for line in (root / "mcp_calls.jsonl").read_text().splitlines()]
    assert [row["tool"] for row in calls] == ["vivado_start", "vivado_execute", "vivado_stop"]
    assert timeouts["vivado_execute"] > 120
    assert timeouts["vivado_stop"] <= 30
