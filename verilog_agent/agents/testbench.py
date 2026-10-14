"""Generate an experimental TB without changing the RTL Generator contract."""

from __future__ import annotations

import json
import re

from verilog_agent.agents.validation import AgentOutputError
from verilog_agent.providers.base import StructuredModelProvider


class RealTestbenchGenerator:
    def __init__(self, provider: StructuredModelProvider):
        self.provider = provider

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
