"""Generate an experimental TB without changing the RTL Generator contract."""

from __future__ import annotations

import json
import re

from rtlforge.agents.validation import AgentOutputError
from rtlforge.domain.models import SpecIR
from rtlforge.providers.base import StructuredModelProvider


def _has_invalid_rs_bm_locator_expectation(source: str) -> bool:
    """Reject obvious literal RS KES locator contradictions, not arbitrary TB expressions."""
    source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.DOTALL)
    for match in re.finditer(
        r"\blocator\s*!==?\s*\d*'([hdb])([0-9a-fA-F_]+)", source, re.IGNORECASE,
    ):
        radix = {"h": 16, "d": 10, "b": 2}[match.group(1).lower()]
        value = int(match.group(2).replace("_", ""), radix)
        if value and (value & 0x3FF) != 1:
            return True
    for call in re.finditer(r"\brun_case\s*\(", source):
        args: list[str] = []
        token: list[str] = []
        depth = 0
        for char in source[call.end():]:
            if char in "({":
                depth += 1
            elif char in ")}":
                if char == ")" and depth == 0:
                    args.append("".join(token).strip())
                    break
                depth -= 1
            elif char == "," and depth == 0:
                args.append("".join(token).strip())
                token = []
                continue
            token.append(char)
        if len(args) < 2:
            continue
        locator = args[1].replace("_", "").strip()
        if not re.fullmatch(r"\d*'[hdb]0*1", locator, re.IGNORECASE):
            continue
        literals = re.findall(r"\b\d*'[hdb]([0-9a-fA-F_]+)", args[0], re.IGNORECASE)
        if any(any(digit != "0" for digit in value.replace("_", "")) for value in literals):
            return True
    return False


class RealTestbenchGenerator:
    def __init__(self, provider: StructuredModelProvider):
        self.provider = provider

    def generate_for_contract(self, contract: SpecIR) -> tuple[str, list[str]]:
        """Generate an advisory self-checking TB without seeing DUT or golden assets."""
        if contract.missing_semantics():
            raise AgentOutputError("Testbench requires a clarified hardware contract")
        rs_bm_guidance = (
            " For RS BM KES, locator=1 is only the zero-error locator: a legal nonzero "
            "syndrome needs a nonconstant locator. If its coefficients cannot be derived "
            "from the contract, test protocol properties instead of guessing exact values."
            if contract.module_name == "rs_kes_bm" else ""
        )
        instructions = (
            "Generate a bounded self-checking Icarus-compatible SystemVerilog module tb "
            "from this frozen hardware contract only. Never assume access to DUT RTL, "
            "golden tests, or reference implementations. Check observable outputs with "
            "case inequality and $fatal(1, ...) on mismatch, include a timeout, and print "
            "GENERATED_TB_PASS only after all checks pass. Keep the TB at most 200 source lines "
            "with 3 to 5 directed scenarios; reuse small tasks instead of repeating vectors. "
            "Do not enumerate all cases or implement a full reference algorithm here; "
            "derive exact expected outputs from the contract before asserting them. For a "
            "nontrivial input, never reuse reset or no-error expected outputs as a guess; "
            "if an exact value cannot be derived, check only justified protocol properties. "
            "The independent golden verification is separate. Return a concise list of test "
            "objectives for the RTL Coder; it must not contain testbench code or vectors. "
            "Return JSON only. This testbench is advisory, never an authoritative oracle."
            " Structured clock/reset, signal_timing, handshakes and parameters are binding. "
            "Drive inputs before the specified sampling edge; observe zero-cycle outputs "
            "after NBA settling on that edge. Never add a cycle or choose a reset default."
            + rs_bm_guidance
        )
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "testbench": {"type": "string"},
                "test_objectives": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["testbench", "test_objectives"],
        }
        for attempt in range(2):
            payload = self.provider.generate_structured(
                instructions=instructions + (
                    " Your previous TB used an inconsistent RS locator expectation. "
                    "A completed BM locator has constant coefficient 1, and a legal nonzero "
                    "syndrome cannot have locator=1. Correct the assertion or omit its exact check."
                    if attempt else ""
                ),
                input_text=json.dumps(contract.to_dict(), ensure_ascii=False),
                schema_name="contract_testbench",
                schema=schema,
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
            if (contract.module_name != "rs_kes_bm"
                    or not _has_invalid_rs_bm_locator_expectation(source)):
                return source.strip() + "\n", [item.strip() for item in objectives]
        raise AgentOutputError("RS BM TB contains an inconsistent locator expectation")

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
