from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from verilog_agent.agents.validation import (
    AgentOutputError,
    require_string_list,
    require_verilog_identifier,
)
from verilog_agent.domain.models import Candidate, RTLTaskContext
from verilog_agent.providers.base import StructuredModelProvider

_SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

GENERATOR_INSTRUCTIONS = """You are the RTL Generator in a Verilog multi-agent system.
Generate complete, synthesizable Verilog-2005 modules that exactly implement the supplied
SpecIR and declared module interface. Return plain RTL inside the JSON field, never Markdown
fences. Do not invent or change ports, the module name, the specification, tests, or oracle.
Candidates must be functionally equivalent but use a stated, controlled structural difference.
Write design_pattern and assumptions in the user's language from SpecIR. Do not call tools or
include a testbench. Use explicit widths and synthesizable constructs."""


class ArtifactRepository:
    """Write generated artifacts only below one configured run root."""

    def __init__(self, root: Path | str = Path("runs")) -> None:
        self.root = Path(root)

    def write_candidate(
        self,
        task_id: str,
        candidate_id: str,
        module_name: str,
        rtl: str,
    ) -> Path:
        for field_name, value in (("task_id", task_id), ("candidate_id", candidate_id)):
            if _SAFE_PATH_SEGMENT.fullmatch(value) is None or value in {".", ".."}:
                raise AgentOutputError(f"unsafe {field_name}: {value!r}")
        safe_module_name = require_verilog_identifier(module_name, "module_name")
        destination = (
            self.root
            / task_id
            / "candidates"
            / candidate_id
            / f"{safe_module_name}.v"
        )
        root = self.root.resolve()
        resolved = destination.resolve()
        if root not in resolved.parents:
            raise AgentOutputError("candidate path escapes the artifact root")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".v.tmp")
        temporary.write_text(rtl, encoding="utf-8", newline="\n")
        temporary.replace(destination)
        return destination


class RealGenerator:
    def __init__(
        self,
        provider: StructuredModelProvider,
        *,
        artifacts: ArtifactRepository | None = None,
    ) -> None:
        self.provider = provider
        self.artifacts = artifacts or ArtifactRepository()

    def generate(self, context: RTLTaskContext, candidate_count: int) -> list[Candidate]:
        if context.spec_ir is None or context.task_tree_root is None:
            raise AgentOutputError("Generator requires a completed Planner result")
        if not 1 <= candidate_count <= 3:
            raise AgentOutputError("real Generator requires one to three candidates")

        schema = self._schema(candidate_count)
        prompt = json.dumps(
            {
                "candidate_count": candidate_count,
                "previous_design_patterns": [
                    candidate.design_pattern for candidate in context.candidates
                    if candidate.parent_id is None
                ],
                "generation_instruction": (
                    "Generate a fresh independent implementation, not a patch."
                ),
                "spec_ir": context.spec_ir.to_dict(),
                "task_tree_root": context.task_tree_root.to_dict(),
                "retrieved_verified_design_patterns": context.metadata.get(
                    "retrieved_design_patterns", []
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        payload = self.provider.generate_structured(
            instructions=GENERATOR_INSTRUCTIONS,
            input_text=prompt,
            schema_name="verilog_rtl_candidates",
            schema=schema,
        )
        raw_candidates = payload.get("candidates")
        if not isinstance(raw_candidates, list) or len(raw_candidates) != candidate_count:
            actual_count = len(raw_candidates) if isinstance(raw_candidates, list) else 0
            raise AgentOutputError(
                f"Generator returned {actual_count} candidates; expected {candidate_count}"
            )

        prepared: list[tuple[str, str, list[str], str]] = []
        seen_hashes: set[str] = {candidate.code_hash for candidate in context.candidates}
        for index, raw_candidate in enumerate(raw_candidates, start=1):
            if not isinstance(raw_candidate, dict):
                raise AgentOutputError(f"candidates[{index - 1}] must be an object")
            rtl = self._validate_rtl(raw_candidate.get("rtl"), context.spec_ir.module_name)
            digest = sha256(rtl.encode("utf-8")).hexdigest()
            if digest in seen_hashes:
                raise AgentOutputError("Generator returned duplicate RTL candidates")
            seen_hashes.add(digest)
            design_pattern = raw_candidate.get("design_pattern")
            if not isinstance(design_pattern, str) or not design_pattern.strip():
                raise AgentOutputError("candidate design_pattern must not be empty")
            assumptions = require_string_list(
                raw_candidate.get("assumptions"), f"candidates[{index - 1}].assumptions"
            )
            prepared.append((rtl, design_pattern.strip(), assumptions, digest))

        candidates = []
        first_index = 1 + sum(candidate.parent_id is None for candidate in context.candidates)
        for index, (rtl, design_pattern, assumptions, digest) in enumerate(
            prepared, start=first_index
        ):
            candidate_id = f"cand_{index:02d}"
            path = self.artifacts.write_candidate(
                context.task_id,
                candidate_id,
                context.spec_ir.module_name,
                rtl,
            )
            candidates.append(
                Candidate(
                    candidate_id=candidate_id,
                    artifact_path=path.as_posix(),
                    code_hash=digest,
                    design_pattern=design_pattern,
                    assumptions=assumptions,
                    metadata={"generated_by": "real_generator", "language": "verilog-2005"},
                )
            )
        return candidates

    @staticmethod
    def _validate_rtl(value: object, module_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise AgentOutputError("candidate RTL must not be empty")
        rtl = value.strip() + "\n"
        if "```" in rtl:
            raise AgentOutputError("candidate RTL must not contain Markdown fences")
        declaration = re.compile(rf"\bmodule\s+{re.escape(module_name)}\b")
        if declaration.search(rtl) is None:
            raise AgentOutputError(
                f"candidate RTL does not declare expected module {module_name!r}"
            )
        return rtl

    @staticmethod
    def _schema(candidate_count: int) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "candidates": {
                    "type": "array",
                    "minItems": candidate_count,
                    "maxItems": candidate_count,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "rtl": {"type": "string", "minLength": 1},
                            "design_pattern": {"type": "string", "minLength": 1},
                            "assumptions": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1},
                            },
                        },
                        "required": ["rtl", "design_pattern", "assumptions"],
                    },
                }
            },
            "required": ["candidates"],
        }
