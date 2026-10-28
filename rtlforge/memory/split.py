"""Back up and migrate a legacy mixed Memory database without reclassifying its contents."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from rtlforge.memory.models import MemoryItem, MemoryType
from rtlforge.memory.typed_store import TypedMemoryStore


def split_database(source: Path, directory: Path) -> dict:
    source, directory = source.resolve(), directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = TypedMemoryStore(directory, toolchain_version="typed-migration-v1")
    if source in target.database_paths:
        raise ValueError("Source must not be a typed destination")
    # Snapshot through SQLite's backup API, including committed WAL pages if present.
    backup_dir = directory / "backups"
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / ("legacy_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".sqlite")
    with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as old:
        with sqlite3.connect(backup) as snapshot:
            old.backup(snapshot)
        rows = old.execute(
            "SELECT payload, status, positive_uses, negative_uses, confidence FROM memory_items"
        ).fetchall()
        episodes = old.execute("SELECT * FROM repair_attempts").fetchall()
    expected = {}
    existing = {item["memory_id"]: item for item in target.list_memories()}
    counts = {kind.value: 0 for kind in MemoryType}
    for payload, status, positive, negative, confidence in rows:
        data = json.loads(payload)
        data.update(status=status, positive_uses=positive, negative_uses=negative,
                    confidence=confidence)
        item = MemoryItem(**data)
        expected[item.memory_id] = item.to_dict()
        counts[item.memory_type] += 1
        if item.memory_id in existing:
            if existing[item.memory_id] != item.to_dict():
                raise ValueError(f"Destination already has a different record: {item.memory_id}")
        else:
            target._upsert_memory(item)
    for kind in MemoryType:
        store = target._store(kind.value)
        with store._connect() as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError(f"Invalid destination: {kind.value}")
    with target._store("REPAIR_STRATEGY")._connect() as connection:
        connection.executemany(
            "INSERT OR IGNORE INTO repair_attempts VALUES (?, ?, ?, ?)", episodes,
        )
    actual = {item["memory_id"]: item for item in target.list_memories()}
    for memory_id, data in expected.items():
        if actual.get(memory_id) != data:
            raise ValueError(f"Migration changed legacy payload: {memory_id}")
    report = {"source": str(source), "backup": str(backup), "by_type": counts,
              "memory_items": len(rows), "repair_attempts": len(episodes),
              "verified": True}
    (directory / "migration_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(split_database(args.source, args.directory), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
