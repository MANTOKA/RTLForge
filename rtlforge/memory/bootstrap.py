"""Seed persistent Memory from existing verified, isolated run databases."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from rtlforge.memory import DEFAULT_MEMORY_DATABASE, MemoryItem, SQLiteMemoryStore

ROOT = Path(__file__).resolve().parents[2]
HUMAN_SOURCE = ROOT / "runs/human-v2/gpt-5.6-terra/state/verilog_agent_memory.sqlite"
RS_SOURCE = ROOT / "runs/rs-272-257/state/verilog_agent_memory.sqlite"


def _items(path: Path) -> list[MemoryItem]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as db:
        payloads = [json.loads(row[0]) for row in db.execute("SELECT payload FROM memory_items")]
    return [MemoryItem(**payload) for payload in payloads]


def bootstrap_memory(target: Path, human_source: Path, rs_source: Path) -> dict[str, int]:
    if target.resolve() in {human_source.resolve(), rs_source.resolve()}:
        raise ValueError("Target Memory must not overwrite a source database")
    human_items = [
        item for item in _items(human_source)
        if item.status == "ACTIVE" and (
            (item.memory_type in {"DESIGN_PATTERN", "REPAIR_STRATEGY"}
             and item.verification_gate_reached == "FUNCTIONAL_VERIFIED")
            or (item.memory_type == "ERROR_PATTERN"
                and item.verification_gate_reached == "VERIFICATION_FAILED_OBSERVED")
        )
    ]
    rs_items = [
        item for item in _items(rs_source)
        if item.memory_type == "HUMAN_INTERVENTION" and item.status == "EVIDENCE_ONLY"
        and item.verification_gate_reached == "FUNCTIONAL_VERIFIED"
    ]
    store = SQLiteMemoryStore(target, toolchain_version="persistent-memory-v1")
    for item in human_items:
        store._upsert_memory(item)
    for item in rs_items:
        store.record_human_intervention(item)
        rule = MemoryItem(
            memory_id="mem_" + hashlib.sha256(
                f"PROJECT_RULE|{item.memory_id}".encode()
            ).hexdigest()[:20],
            memory_type="PROJECT",
            summary=item.summary,
            error_signature=item.error_signature,
            module_family=item.module_family,
            applicability_conditions=item.applicability_conditions,
            recommended_action=item.recommended_action,
            source_task_id=item.source_task_id,
            source_candidate_id=item.source_candidate_id,
            source_problem_id=None,
            before_score=None,
            after_score=None,
            verification_gate_reached="CURATED_VERIFIED_RULE",
            toolchain_version=item.toolchain_version,
            evidence_hashes=list(item.evidence_hashes),
            provenance={
                "derived_from_memory_id": item.memory_id,
                "operator": item.provenance["operator"],
                "agent_generated": False,
            },
        )
        store.record_project_rule(rule)
    return {"human_active": len(human_items), "rs_evidence": len(rs_items),
            "rs_curated_rules": len(rs_items), "total": len(store.list_memories())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_MEMORY_DATABASE)
    parser.add_argument("--human-source", type=Path, default=HUMAN_SOURCE)
    parser.add_argument("--rs-source", type=Path, default=RS_SOURCE)
    args = parser.parse_args()
    print(json.dumps(
        bootstrap_memory(args.database, args.human_source, args.rs_source),
        ensure_ascii=False, sort_keys=True,
    ))


if __name__ == "__main__":
    main()
