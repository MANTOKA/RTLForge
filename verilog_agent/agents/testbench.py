"""Generate an experimental TB without changing the RTL Generator contract."""

from __future__ import annotations

import json
import re

from verilog_agent.agents.validation import AgentOutputError
from verilog_agent.domain.models import SpecIR
from verilog_agent.providers.base import StructuredModelProvider


class RealTestbenchGenerator:
    def __init__(self, provider: StructuredModelProvider):
        self.provider = provider

    def generate_for_contract(self, contract: SpecIR) -> tuple[str, list[str]]:
        """Generate an advisory self-checking TB without seeing DUT or golden assets."""
        payload = self.provider.generate_structured(
            instructions=(
                "Generate a bounded self-checking Icarus-compatible SystemVerilog module tb "
                "from this frozen hardware contract only. Never assume access to DUT RTL, "
                "golden tests, or reference implementations. Check observable outputs with "
                "case inequality and $fatal(1, ...) on mismatch, include a timeout, and print "
                "GENERATED_TB_PASS only after all checks pass. Return a concise list of test "
                "objectives for the RTL Coder; it must not contain testbench code or vectors. "
                "Return JSON only. This testbench is advisory, never an authoritative oracle."
            ),
            input_text=json.dumps(contract.to_dict(), ensure_ascii=False),
            schema_name="contract_testbench",
            schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "testbench": {"type": "string"},
                    "test_objectives": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["testbench", "test_objectives"],
            },
        )
        source = payload.get("testbench")
        objectives = payload.get("test_objectives")
        if (
            not isinstance(source, str)
            or "```" in source
            or not re.search(r"\bmodule\s+tb\b", source)
            or "$fatal" not in source
            or "GENERATED_TB_PASS" not in source
            or not isinstance(objectives, list)
            or not objectives
            or not all(isinstance(item, str) and item.strip() for item in objectives)
        ):
            raise AgentOutputError("contract TB requires self-checks and test objectives")
        return source.strip() + "\n", [item.strip() for item in objectives]

    def generate(self, specification: str, cases: list[dict]) -> str:
        payload = self.provider.generate_structured(
            instructions=(
                "Generate a self-checking SystemVerilog testbench module tb. "
                "Target the project's Icarus simulator: avoid break and continue; "
                "use bounded while/for loops with explicit conditions instead. "
                "Use the frozen specification and supplied vectors only; no DUT implementation "
                "is available. Embed vector constants, no files or external includes. "
                "Drive inputs on falling edges, sample after rising edges with #1. "
                "Check every expected output with case inequality, completion and bounded "
                "latency. Include global timeout. $fatal(1, ...) on any failure; "
                "print RS_AGENT_TB_PASS only after ALL cases, then $finish. "
                "Return JSON with testbench only, no Markdown. This generated TB will "
                "be evaluated separately; it is not an authoritative oracle."
            ),
            input_text=json.dumps({"specification": specification, "vectors": cases}),
            schema_name="rs_testbench",
            schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"testbench": {"type": "string"}},
                "required": ["testbench"],
            },
        )
        text = payload.get("testbench")
        if not isinstance(text, str) or "```" in text or not re.search(r"\bmodule\s+tb\b", text):
            raise AgentOutputError("TB generator must return plain module tb source")
        return text.strip() + "\n"
