from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from verilog_agent.domain.models import Event, RTLTaskContext


class SQLiteTaskStore:
    """Small SQLite event log and versioned checkpoint repository."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS events (
                    task_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (task_id, sequence)
                );

                CREATE TABLE IF NOT EXISTS checkpoints (
                    task_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    PRIMARY KEY (task_id, version)
                );
                """
            )

    def append_event(self, event: Event) -> Event:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
                "FROM events WHERE task_id = ?",
                (event.task_id,),
            ).fetchone()
            sequence = int(row["next_sequence"])
            stored = replace(event, sequence=sequence)
            connection.execute(
                """
                INSERT INTO events (
                    task_id, sequence, event_id, event_type, stage, timestamp, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.task_id,
                    stored.sequence,
                    stored.event_id,
                    stored.event_type.value,
                    stored.stage.value,
                    stored.timestamp,
                    json.dumps(stored.payload, sort_keys=True),
                ),
            )
        return stored

    def list_events(self, task_id: str) -> list[Event]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE task_id = ? ORDER BY sequence",
                (task_id,),
            ).fetchall()
        return [
            Event.from_dict(
                {
                    "task_id": row["task_id"],
                    "sequence": row["sequence"],
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "stage": row["stage"],
                    "timestamp": row["timestamp"],
                    "payload": json.loads(row["payload_json"]),
                }
            )
            for row in rows
        ]

    def save_checkpoint(self, context: RTLTaskContext) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next_version "
                "FROM checkpoints WHERE task_id = ?",
                (context.task_id,),
            ).fetchone()
            version = int(row["next_version"])
            context.checkpoint_version = version
            connection.execute(
                """
                INSERT INTO checkpoints (task_id, version, stage, context_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    context.task_id,
                    version,
                    context.current_stage.value,
                    json.dumps(context.to_dict(), sort_keys=True),
                ),
            )
        return version

    def load_latest_checkpoint(self, task_id: str) -> RTLTaskContext | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT context_json FROM checkpoints
                WHERE task_id = ? ORDER BY version DESC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return RTLTaskContext.from_dict(json.loads(row["context_json"]))
