"""Oracle-playback fixtures validate the checker, NOT an RS RTL implementation."""

import pytest

from verilog_agent.benchmarks.rs_demo import RSRunner, golden_tb, packed
from verilog_agent.benchmarks.rs_reference import bm, ribm, vectors

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("algorithm", ["bm", "ribm"])
def test_checker_accepts_playback_rejects_mutants(tmp_path, algorithm):
    cases = []
    entries = []
    for i, case in enumerate(vectors()):
        s = case["syndromes"][:14]
        locator, evaluator = (bm if algorithm == "bm" else ribm)(s)
        cases.append(
            {
                "syndrome_hex": f"{packed(s):x}",
                "locator_hex": f"{packed(locator):x}",
                "evaluator_hex": f"{packed(evaluator):x}",
            }
        )
        entries.append(
            f"{i}: begin if(syndromes!==140'h{packed(s):x}) "
            f'$fatal(1,"stimulus mismatch"); '
            f"expected_l<=80'h{packed(locator):x}; "
            f"expected_e<=70'h{packed(evaluator):x}; end"
        )
    module = f"rs_kes_{algorithm}"
    fixture = f"""module {module}(input clk,rst,start,input [139:0] syndromes,
output reg busy,done,output reg [79:0] locator,output reg [69:0] evaluator);
integer count; reg [79:0] expected_l; reg [69:0] expected_e;
always @(posedge clk) begin
 if(rst) begin busy<=0;done<=0;locator<=0;evaluator<=0;count<=0; end
 else begin
 done<=0;
 if(start && !busy) begin
 busy<=1;
 case(count)
 {chr(10).join(entries)}
 default: $fatal(1,"unexpected extra case");
 endcase
 count<=count+1;
 end else if(busy) begin
 busy<=0;done<=1;locator<=expected_l;evaluator<=expected_e;
 end
 end
end
endmodule
"""
    tb = tmp_path / "golden_tb.sv"
    tb.write_text(golden_tb(module, cases), encoding="utf-8")
    runner = RSRunner(tmp_path)
    for name, source, status in [
        ("playback", fixture, "PASS"),
        (
            "corrupt",
            fixture.replace("evaluator<=expected_e;", "evaluator<=expected_e^70'd1;"),
            "FAIL",
        ),
        ("no_done", fixture.replace("done<=1;", "done<=0;"), "FAIL"),
        ("early_finish", fixture.replace("endmodule", "initial #2 $finish; endmodule"), "FAIL"),
    ]:
        rtl = tmp_path / f"{name}.v"
        rtl.write_text(source, encoding="utf-8")
        result = runner.run_files("simulate", [tb, rtl])
        assert result["status"] == status, (name, result)
