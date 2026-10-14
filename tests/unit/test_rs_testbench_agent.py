import pytest

from verilog_agent.agents.testbench import RealTestbenchGenerator
from verilog_agent.agents.validation import AgentOutputError


class CaptureProvider:
    def __init__(self, source):
        self.source = source
        self.request = None

    def generate_structured(self, **kwargs):
        self.request = kwargs
        return {"testbench": self.source}


def test_tb_generation_contract():
    provider = CaptureProvider("module tb; endmodule")
    result = RealTestbenchGenerator(provider).generate("frozen spec", [{"id": "case0"}])
    assert result == "module tb; endmodule\n"
    assert "frozen spec" in provider.request["input_text"]
    assert "case0" in provider.request["input_text"]
    assert "no DUT implementation" in provider.request["instructions"]


@pytest.mark.parametrize(
    "source", [None, "", "```module tb; endmodule```", "module dut; endmodule"]
)
def test_invalid_tb_output(source):
    with pytest.raises(AgentOutputError):
        RealTestbenchGenerator(CaptureProvider(source)).generate("spec", [])
