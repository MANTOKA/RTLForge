"""Real Docker checks; scripted providers do not consume model quota."""

from __future__ import annotations

import json

import pytest

from rtlforge import console, providers
from rtlforge.testing import ScriptedProvider, counter_candidate_payload, counter_plan_payload
from tests.unit.test_integration_entry import module_fixture


@pytest.mark.integration
def test_generic_menu_pipeline_real_docker(tmp_path, monkeypatch):
    modules = [module_fixture(tmp_path / f"source{index}", f"reusable_counter{index}")[1]
               for index in range(3)]
    monkeypatch.setattr(console, "ROOT", tmp_path)
    env = tmp_path / ".env.test"
    env.write_text("OPENAI_API_KEY=scripted\nRTLFORGE_MODEL=scripted\n")
    tb = tmp_path / "tb.sv"
    tb.write_text('''module tb;
reg clk=0; always #5 clk=~clk;
reg rst=1; wire [3:0] q;
counter dut(.clk(clk),.rst(rst),.q(q));
integer i;
initial begin
 repeat(2) @(negedge clk);
 if(q!==0) $fatal(1,"reset mismatch");
 rst=0;
 for(i=1;i<=40;i=i+1) begin
  @(posedge clk); #1;
  if(q!==(i%16)) $fatal(1,"increment/wrap mismatch");
 end
 $display("GENERIC_INTEGRATION_PASS"); $finish;
end
initial begin #1000; $fatal(1,"timeout"); end
endmodule
''')
    candidate = counter_candidate_payload()["candidates"][0]
    candidate["rtl"] = (
        "module counter(input clk,rst,output [3:0] q); wire [3:0] q0,q1,q2; "
        "reusable_counter0 unit0(.clk(clk),.rst(rst),.q(q0)); "
        "reusable_counter1 unit1(.clk(clk),.rst(rst),.q(q1)); "
        "reusable_counter2 unit2(.clk(clk),.rst(rst),.q(q2)); "
        "assign q=q0^q1^q2; endmodule"
    )
    provider = ScriptedProvider(counter_plan_payload(), {"candidates": [candidate]})
    monkeypatch.setattr(providers, "OpenAIResponsesProvider", lambda _: provider)
    console.run_requirement("Reuse an existing counter as a top", console.Settings(
        env_file=str(env), candidates=1, max_repairs=0, memory_mode="off",
    ), tb, modules=modules)
    source = next((tmp_path / "runs").rglob("context.json"))
    state = json.loads(source.read_text(encoding="utf-8"))
    assert state["current_stage"] == "COMPLETED"
    assert state["metadata"]["golden_tb_pass"] is True
    assert "GENERIC_INTEGRATION_PASS" in next(
        source.parent.rglob("cand_01_simulate.json")
    ).read_text(encoding="utf-8")
