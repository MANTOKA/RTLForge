from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from verilog_agent.memory.models import MemoryItem, MemoryType, RepairAttempt, _now


class SQLiteMemoryStore:
    """Store repair episodes and retrieve only verification-gated strategy summaries."""

    def __init__(self, path: Path, *, toolchain_version: str) -> None:
        self.path = path
        self.toolchain_version = toolchain_version
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def record_repair_attempt(self, attempt: RepairAttempt) -> MemoryItem | None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO repair_attempts VALUES (?, ?, ?, ?)",
                (
                    attempt.attempt_id,
                    attempt.task_id,
                    int(attempt.passed),
                    json.dumps(attempt.to_dict(), ensure_ascii=False),
                ),
            )
        if not attempt.passed or attempt.after_mismatches not in {0, None}:
            return None
        if not (attempt.rationale.strip() or attempt.expected_impact.strip()):
            return None
        item = self._consolidate(attempt)
        self._upsert_memory(item)
        return item

    def record_error_observation(
        self,
        *,
        task_id: str,
        candidate_id: str,
        error_signature: str,
        module_family: str,
        source_problem_id: str | None,
        summary: str,
        evidence_path: str | None,
    ) -> MemoryItem:
        """Record a grounded failure pattern without presenting it as a repair."""
        item = self._memory_item(
            memory_type=MemoryType.ERROR_PATTERN,
            identity=f"{error_signature}|{module_family}",
            summary=summary,
            error_signature=error_signature,
            module_family=module_family,
            applicability_conditions=(
                f"Observed for {module_family}; use as diagnostic evidence only."
            ),
            recommended_action="Inspect this failure pattern before choosing a repair strategy.",
            source_task_id=task_id,
            source_candidate_id=candidate_id,
            source_problem_id=source_problem_id,
            verification_gate_reached="VERIFICATION_FAILED_OBSERVED",
            evidence_paths=[evidence_path],
            confidence=0.55,
        )
        self._upsert_memory(item)
        return item

    def record_verified_design_pattern(
        self,
        *,
        task_id: str,
        candidate_id: str,
        module_family: str,
        source_problem_id: str | None,
        design_pattern: str,
        evidence_path: str | None,
    ) -> MemoryItem | None:
        """Promote only a non-empty pattern attached to functionally verified RTL."""
        pattern = design_pattern.strip()
        if not pattern:
            return None
        item = self._memory_item(
            memory_type=MemoryType.DESIGN_PATTERN,
            identity=f"{module_family}|{pattern}",
            summary=f"Verified {module_family} design pattern: {pattern}",
            error_signature="",
            module_family=module_family,
            applicability_conditions=f"Use for {module_family} designs with a matching interface.",
            recommended_action=pattern,
            source_task_id=task_id,
            source_candidate_id=candidate_id,
            source_problem_id=source_problem_id,
            verification_gate_reached="FUNCTIONAL_VERIFIED",
            evidence_paths=[evidence_path],
            confidence=0.7,
        )
        self._upsert_memory(item)
        return item

    def retrieve(
        self,
        *,
        error_signature: str,
        module_family: str,
        exclude_problem_id: str | None,
        limit: int = 3,
        memory_types: tuple[str, ...] = (
            MemoryType.REPAIR_STRATEGY.value,
            MemoryType.ERROR_PATTERN.value,
        ),
    ) -> list[dict[str, Any]]:
        terms = re.findall(r"[A-Za-z0-9_]+", f"{error_signature} {module_family}")
        if not terms:
            return []
        query = " OR ".join(f'"{term}"' for term in terms)
        sql = """
            SELECT m.payload
            FROM memory_items_fts AS f
            JOIN memory_items AS m ON m.memory_id = f.memory_id
            WHERE memory_items_fts MATCH ?
              AND m.status = 'ACTIVE'
            ORDER BY bm25(memory_items_fts), m.confidence DESC
            LIMIT ?
        """
        with self._connect() as connection:
            rows = connection.execute(sql, (query, max(limit * 10, 50))).fetchall()
        allowed_types = set(memory_types)
        eligible: list[tuple[int, dict[str, Any]]] = []
        for rank, row in enumerate(rows):
            payload = json.loads(row[0])
            source_ids = set(payload.get("source_problem_ids", []))
            if payload.get("source_problem_id"):
                source_ids.add(payload["source_problem_id"])
            if exclude_problem_id is not None and exclude_problem_id in source_ids:
                continue
            if payload.get("memory_type") not in allowed_types:
                continue
            if payload.get("module_family") != module_family:
                continue
            eligible.append((rank, payload))
        type_priority = {
            MemoryType.REPAIR_STRATEGY.value: 0,
            MemoryType.DESIGN_PATTERN.value: 1,
            MemoryType.ERROR_PATTERN.value: 2,
        }
        eligible.sort(
            key=lambda item: (type_priority.get(str(item[1].get("memory_type")), 9), item[0])
        )
        return [self._safe_retrieval(payload) for _, payload in eligible[:limit]]

    def retrieve_design_patterns(
        self,
        *,
        module_family: str,
        exclude_problem_id: str | None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        return self.retrieve(
            error_signature=module_family,
            module_family=module_family,
            exclude_problem_id=exclude_problem_id,
            limit=limit,
            memory_types=(MemoryType.DESIGN_PATTERN.value,),
        )

    def record_use(self, memory_ids: list[str], *, positive: bool) -> None:
        if not memory_ids:
            return
        column = "positive_uses" if positive else "negative_uses"
        with self._connect() as connection:
            for memory_id in memory_ids:
                connection.execute(
                    f"UPDATE memory_items SET {column} = {column} + 1, last_used_at = ? "
                    "WHERE memory_id = ?",
                    (_now(), memory_id),
                )
                connection.execute(
                    "UPDATE memory_items SET status = 'QUARANTINED' "
                    "WHERE memory_id = ? AND negative_uses >= 2 AND negative_uses > positive_uses",
                    (memory_id,),
                )
            self._refresh_payloads(connection, memory_ids)

    def list_attempts(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM repair_attempts ORDER BY rowid"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def list_memories(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM memory_items ORDER BY created_at"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def supersede_coarse_repair_strategies(self) -> int:
        """Disable generic copies when the same problem has a refined family entry."""
        memories = self.list_memories()

        def sources(item: dict[str, Any]) -> set[str]:
            values = set(item.get("source_problem_ids", []))
            if item.get("source_problem_id"):
                values.add(str(item["source_problem_id"]))
            return values

        refined_sources = {
            problem_id
            for item in memories
            if item.get("memory_type") == MemoryType.REPAIR_STRATEGY.value
            and item.get("module_family") != "generic_rtl"
            for problem_id in sources(item)
        }
        superseded = [
            item["memory_id"]
            for item in memories
            if item.get("memory_type") == MemoryType.REPAIR_STRATEGY.value
            and item.get("module_family") == "generic_rtl"
            and item.get("status") != "SUPERSEDED"
            and refined_sources.intersection(sources(item))
        ]
        if not superseded:
            return 0
        with self._connect() as connection:
            for memory_id in superseded:
                connection.execute(
                    "UPDATE memory_items SET status = 'SUPERSEDED' WHERE memory_id = ?",
                    (memory_id,),
                )
            self._refresh_payloads(connection, superseded)
        return len(superseded)

    def _consolidate(self, attempt: RepairAttempt) -> MemoryItem:
        action = attempt.rationale.strip() or attempt.expected_impact.strip()
        digest = hashlib.sha256(
            f"{attempt.failure_category}|{attempt.module_family}|{action}".encode()
        ).hexdigest()[:20]
        evidence_hashes = [
            value
            for value in (
                self._file_hash(attempt.diff_path),
                self._file_hash(attempt.evidence_path),
            )
            if value is not None
        ]
        return MemoryItem(
            memory_id=f"mem_{digest}",
            memory_type=MemoryType.REPAIR_STRATEGY.value,
            summary=(
                f"Verified repair for {attempt.failure_category} in "
                f"{attempt.module_family}: {action}"
            ),
            error_signature=attempt.error_signature,
            module_family=attempt.module_family,
            applicability_conditions=(
                f"Use only for {attempt.module_family} designs with "
                f"{attempt.failure_category}; always re-run current verification."
            ),
            recommended_action=action,
            source_task_id=attempt.task_id,
            source_candidate_id=attempt.candidate_id,
            source_problem_id=attempt.benchmark_problem_id,
            before_score=(
                float(attempt.before_mismatches)
                if attempt.before_mismatches is not None
                else None
            ),
            after_score=(
                float(attempt.after_mismatches)
                if attempt.after_mismatches is not None
                else None
            ),
            verification_gate_reached="FUNCTIONAL_VERIFIED",
            toolchain_version=self.toolchain_version,
            evidence_hashes=evidence_hashes,
            source_problem_ids=(
                [attempt.benchmark_problem_id] if attempt.benchmark_problem_id else []
            ),
            source_task_ids=[attempt.task_id],
        )

    def _memory_item(
        self,
        *,
        memory_type: MemoryType,
        identity: str,
        summary: str,
        error_signature: str,
        module_family: str,
        applicability_conditions: str,
        recommended_action: str,
        source_task_id: str,
        source_candidate_id: str,
        source_problem_id: str | None,
        verification_gate_reached: str,
        evidence_paths: list[str | None],
        confidence: float,
    ) -> MemoryItem:
        digest = hashlib.sha256(f"{memory_type.value}|{identity}".encode()).hexdigest()[:20]
        evidence_hashes = [
            digest_value
            for digest_value in (self._file_hash(path) for path in evidence_paths)
            if digest_value is not None
        ]
        return MemoryItem(
            memory_id=f"mem_{digest}",
            memory_type=memory_type.value,
            summary=summary,
            error_signature=error_signature,
            module_family=module_family,
            applicability_conditions=applicability_conditions,
            recommended_action=recommended_action,
            source_task_id=source_task_id,
            source_candidate_id=source_candidate_id,
            source_problem_id=source_problem_id,
            before_score=None,
            after_score=None,
            verification_gate_reached=verification_gate_reached,
            toolchain_version=self.toolchain_version,
            evidence_hashes=evidence_hashes,
            source_problem_ids=[source_problem_id] if source_problem_id else [],
            source_task_ids=[source_task_id],
            confidence=confidence,
        )

    def _upsert_memory(self, item: MemoryItem) -> None:
        payload = json.dumps(item.to_dict(), ensure_ascii=False)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT payload, positive_uses, negative_uses, status FROM memory_items "
                "WHERE memory_id = ?",
                (item.memory_id,),
            ).fetchone()
            if existing is not None:
                merged = json.loads(existing[0])
                problem_ids = set(merged.get("source_problem_ids", []))
                if merged.get("source_problem_id"):
                    problem_ids.add(merged["source_problem_id"])
                new_problem_support = bool(
                    item.source_problem_id and item.source_problem_id not in problem_ids
                )
                problem_ids.update(item.source_problem_ids)
                task_ids = set(merged.get("source_task_ids", []))
                if merged.get("source_task_id"):
                    task_ids.add(merged["source_task_id"])
                new_support = (
                    new_problem_support
                    if item.source_problem_id
                    else item.source_task_id not in task_ids
                )
                task_ids.update(item.source_task_ids)
                evidence_hashes = set(merged.get("evidence_hashes", []))
                evidence_hashes.update(item.evidence_hashes)
                support_count = int(merged.get("support_count", 1)) + int(new_support)
                confidence = max(float(merged.get("confidence", 0.5)), item.confidence)
                if new_support:
                    confidence = min(0.95, confidence + 0.05)
                merged.update(
                    source_problem_ids=sorted(problem_ids),
                    source_task_ids=sorted(task_ids),
                    evidence_hashes=sorted(evidence_hashes),
                    support_count=support_count,
                    confidence=confidence,
                    positive_uses=existing[1],
                    negative_uses=existing[2],
                    status=existing[3],
                )
                payload = json.dumps(merged, ensure_ascii=False)
                connection.execute(
                    "UPDATE memory_items SET confidence = ?, payload = ? WHERE memory_id = ?",
                    (confidence, payload, item.memory_id),
                )
                connection.execute(
                    "DELETE FROM memory_items_fts WHERE memory_id = ?", (item.memory_id,)
                )
                self._insert_fts(connection, merged)
                return
            connection.execute(
                """
                INSERT OR IGNORE INTO memory_items (
                    memory_id, source_problem_id, confidence, positive_uses,
                    negative_uses, created_at, last_used_at, status, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.memory_id,
                    item.source_problem_id,
                    item.confidence,
                    item.positive_uses,
                    item.negative_uses,
                    item.created_at,
                    item.last_used_at,
                    item.status,
                    payload,
                ),
            )
            self._insert_fts(connection, item.to_dict())

    @staticmethod
    def _insert_fts(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        connection.execute(
            "INSERT INTO memory_items_fts VALUES (?, ?, ?, ?, ?, ?)",
            (
                payload["memory_id"],
                payload["summary"],
                payload["error_signature"],
                payload["module_family"],
                payload["applicability_conditions"],
                payload["recommended_action"],
            ),
        )

    @staticmethod
    def _safe_retrieval(payload: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "memory_id",
            "memory_type",
            "summary",
            "error_signature",
            "module_family",
            "applicability_conditions",
            "recommended_action",
            "source_task_id",
            "verification_gate_reached",
            "toolchain_version",
            "evidence_hashes",
            "source_problem_ids",
            "source_task_ids",
            "support_count",
            "confidence",
        )
        return {key: payload.get(key) for key in keys}

    @staticmethod
    def _file_hash(path_value: str | None) -> str | None:
        if not path_value:
            return None
        path = Path(path_value)
        if not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _refresh_payloads(connection: sqlite3.Connection, memory_ids: list[str]) -> None:
        for memory_id in memory_ids:
            row = connection.execute(
                "SELECT positive_uses, negative_uses, last_used_at, status, payload "
                "FROM memory_items WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                continue
            payload = json.loads(row[4])
            payload.update(
                positive_uses=row[0],
                negative_uses=row[1],
                last_used_at=row[2],
                status=row[3],
            )
            connection.execute(
                "UPDATE memory_items SET payload = ? WHERE memory_id = ?",
                (json.dumps(payload, ensure_ascii=False), memory_id),
            )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS repair_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_items (
                    memory_id TEXT PRIMARY KEY,
                    source_problem_id TEXT,
                    confidence REAL NOT NULL,
                    positive_uses INTEGER NOT NULL,
                    negative_uses INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts USING fts5(
                    memory_id UNINDEXED,
                    summary,
                    error_signature,
                    module_family,
                    applicability_conditions,
                    recommended_action
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
