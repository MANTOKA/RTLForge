from pathlib import Path

from rtlforge.domain import PortDirection, PortSpec, SpecIR
from rtlforge.services.interface_check import check_rtl_interface


def _spec() -> SpecIR:
    return SpecIR(
        module_name="counter",
        raw_spec="counter",
        ports=[
            PortSpec("clk", PortDirection.INPUT),
            PortSpec("rst_n", PortDirection.INPUT),
            PortSpec("count", PortDirection.OUTPUT, 8),
        ],
    )


def test_interface_check_accepts_ansi_ports(tmp_path: Path) -> None:
    rtl = tmp_path / "counter.sv"
    rtl.write_text(
        """module counter (
  input logic clk,
  input logic rst_n,
  output logic [7:0] count
);
endmodule
""",
        encoding="utf-8",
    )

    result = check_rtl_interface(rtl, _spec())

    assert result["status"] == "PASS"
    assert result["actual_ports"]["count"]["width"] == 8


def test_interface_check_reports_direction_width_and_extra_port(tmp_path: Path) -> None:
    rtl = tmp_path / "counter.v"
    rtl.write_text(
        """module counter(clk, rst_n, count, debug);
input clk, rst_n;
input [3:0] count;
output debug;
endmodule
""",
        encoding="utf-8",
    )

    result = check_rtl_interface(rtl, _spec())

    assert result["status"] == "FAIL"
    assert result["category"] == "INTERFACE_MISMATCH"
    assert "unexpected ports: debug" in result["errors"]
    assert "port count direction is input, expected output" in result["errors"]
    assert "port count width is 4, expected 8" in result["errors"]
