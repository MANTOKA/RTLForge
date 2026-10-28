"""Graph checkpoints, completed-node receipts and cancellation, separate from audit SQLite."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


class GraphRuntimeStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.checkpointer = SqliteSaver(self.connection)
        self.checkpointer.setup()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS completed_nodes (
                    task_id TEXT NOT NULL, step INTEGER NOT NULL, node TEXT NOT NULL,
                    result TEXT NOT NULL, PRIMARY KEY(task_id, step, node)
                );
                CREATE TABLE IF NOT EXISTS task_controls (
                    task_id TEXT PRIMARY KEY, cancel_requested INTEGER NOT NULL DEFAULT 0
                );
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def load_result(self, task_id: str, step: int, node: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT result FROM completed_nodes WHERE task_id=? AND step=? AND node=?",
                (task_id, step, node),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save_result(self, task_id: str, step: int, node: str, result: dict) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO completed_nodes VALUES (?, ?, ?, ?)",
                (task_id, step, node, json.dumps(result, ensure_ascii=False)),
            )

    def request_cancel(self, task_id: str) -> bool:
        with self.connect() as db:
            cursor = db.execute("INSERT OR IGNORE INTO task_controls VALUES (?, 1)", (task_id,))
        return bool(cursor.rowcount)

    def is_cancelled(self, task_id: str) -> bool:
        with self.connect() as db:
            row = db.execute(
                "SELECT cancel_requested FROM task_controls WHERE task_id=?", (task_id,)
            ).fetchone()
        return bool(row and row[0])

    def close(self) -> None:
        self.connection.close()
