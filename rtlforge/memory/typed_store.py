"""Five appendable indexed databases, selected by memory type rather than task."""

from __future__ import annotations

from pathlib import Path

from rtlforge.memory.models import MemoryItem, MemoryType, RepairAttempt
from rtlforge.memory.store import SQLiteMemoryStore

DEBUGGER_TYPES = ("REPAIR_STRATEGY", "ERROR_PATTERN", "PROJECT")


class TypedMemoryStore:
    def __init__(self, path: Path, *, toolchain_version: str):
        self.path = path
        self.toolchain_version = toolchain_version
        self.path.mkdir(parents=True, exist_ok=True)
        self._stores: dict[str, SQLiteMemoryStore] = {}

    @property
    def database_paths(self) -> list[Path]:
        return [self.path / f"{kind.value}.sqlite" for kind in MemoryType]

    def _store(self, kind: str) -> SQLiteMemoryStore:
        kind = MemoryType(kind).value
        if kind not in self._stores:
            self._stores[kind] = SQLiteMemoryStore(
                self.path / f"{kind}.sqlite", toolchain_version=self.toolchain_version,
            )
        return self._stores[kind]

    def _existing(self, kinds):
        for kind in kinds:
            kind = MemoryType(kind).value
            if (self.path / f"{kind}.sqlite").is_file():
                yield self._store(kind)

    def _upsert_memory(self, item: MemoryItem) -> None:
        self._store(item.memory_type)._upsert_memory(item)

    def record_repair_attempt(self, attempt: RepairAttempt):
        return self._store("REPAIR_STRATEGY").record_repair_attempt(attempt)

    def record_error_observation(self, **kwargs):
        return self._store("ERROR_PATTERN").record_error_observation(**kwargs)

    def record_verified_design_pattern(self, **kwargs):
        return self._store("DESIGN_PATTERN").record_verified_design_pattern(**kwargs)

    def record_human_intervention(self, item):
        return self._store("HUMAN_INTERVENTION").record_human_intervention(item)

    def record_project_rule(self, item):
        return self._store("PROJECT").record_project_rule(item)

    def retrieve(
        self, *, error_signature: str, module_family: str, exclude_problem_id: str | None,
        limit: int = 3, memory_types: tuple[str, ...] = DEBUGGER_TYPES,
    ):
        if limit < 1:
            return []
        selected = {MemoryType(kind).value for kind in memory_types}
        # Preserve the existing type priority without comparing BM25 across databases.
        priority = ("REPAIR_STRATEGY", "DESIGN_PATTERN", "PROJECT", "ERROR_PATTERN")
        ordered = [kind for kind in priority
                   if kind in selected]
        found = []
        for store in self._existing(ordered):
            found.extend(store.retrieve(
                error_signature=error_signature, module_family=module_family,
                exclude_problem_id=exclude_problem_id, limit=limit - len(found),
                memory_types=memory_types,
            ))
            if len(found) >= limit:
                break
        return found

    def retrieve_design_patterns(self, *, module_family, exclude_problem_id, limit=3):
        return self.retrieve(
            error_signature=module_family, module_family=module_family,
            exclude_problem_id=exclude_problem_id, limit=limit,
            memory_types=("DESIGN_PATTERN", "PROJECT"),
        )

    def record_use(self, memory_ids: list[str], *, positive: bool):
        for store in self._existing(kind.value for kind in MemoryType):
            # Indexed ID updates; no memory payload dump is needed to find an ID.
            store.record_use(memory_ids, positive=positive)

    def list_attempts(self):
        return [item for store in self._existing(("REPAIR_STRATEGY",))
                for item in store.list_attempts()]

    def list_memories(self):
        # Administrative operation only; normal retrieval never calls this method.
        return [item for store in self._existing(kind.value for kind in MemoryType)
                for item in store.list_memories()]

    def supersede_coarse_repair_strategies(self):
        return sum(store.supersede_coarse_repair_strategies()
                   for store in self._existing(("REPAIR_STRATEGY",)))
