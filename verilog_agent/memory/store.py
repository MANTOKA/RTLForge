from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from verilog_agent.memory.models import MemoryItem, RepairAttempt, _now


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

    def retrieve(
        self,
        *,
        error_signature: str,
        module_family: str,
        exclude_problem_id: str | None,
        limit: int = 3,
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
              AND (? IS NULL OR m.source_problem_id IS NULL OR m.source_problem_id != ?)
            ORDER BY bm25(memory_items_fts), m.confidence DESC
            LIMIT ?
        """
        with self._connect() as connection:
            rows = connection.execute(
                sql, (query, exclude_problem_id, exclude_problem_id, limit)
            ).fetchall()
        return [self._safe_retrieval(json.loads(row[0])) for row in rows]

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
            memory_type="REPAIR_STRATEGY",
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
        )

    def _upsert_memory(self, item: MemoryItem) -> None:
        payload = json.dumps(item.to_dict(), ensure_ascii=False)
        with self._connect() as connection:
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
            connection.execute(
                "INSERT OR IGNORE INTO memory_items_fts VALUES (?, ?, ?, ?, ?, ?)",
                (
                    item.memory_id,
                    item.summary,
                    item.error_signature,
                    item.module_family,
                    item.applicability_conditions,
                    item.recommended_action,
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
