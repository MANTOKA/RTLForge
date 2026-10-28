from __future__ import annotations

from dataclasses import replace

from rtlforge.memory import MemoryType, SQLiteMemoryStore
from rtlforge.memory.split import split_database
from rtlforge.memory.typed_store import TypedMemoryStore
from tests.unit.test_memory_store import _attempt


def test_split_preserves_items_episodes_and_is_repeatable(tmp_path):
    source = tmp_path / "legacy.sqlite"
    legacy = SQLiteMemoryStore(source, toolchain_version="test")
    legacy.record_repair_attempt(_attempt(passed=False))
    legacy.record_repair_attempt(_attempt(passed=True, after_mismatches=0))
    legacy.record_error_observation(
        task_id="task_fail", candidate_id="cand_01", error_signature="TIMEOUT",
        module_family="counter", source_problem_id=None, summary="Timeout observed",
        evidence_path=None,
    )
    before = legacy.list_memories()
    directory = tmp_path / "typed"
    report = split_database(source, directory)
    store = SQLiteMemoryStore(directory, toolchain_version="test")
    assert isinstance(store, TypedMemoryStore)
    assert len(store.list_attempts()) == 2
    assert sorted(store.list_memories(), key=lambda x: x["memory_id"]) == sorted(
        before, key=lambda x: x["memory_id"],
    )
    assert report["verified"]
    assert all((directory / f"{kind.value}.sqlite").is_file() for kind in MemoryType)
    split_database(source, directory)
    assert len(store.list_attempts()) == 2
    assert len(store.list_memories()) == len(before)
    # Another completed attempt appends a new episode rather than rewriting old ones.
    store.record_repair_attempt(_attempt(passed=False))
    assert len(store.list_attempts()) == 3
    assert len(legacy.list_attempts()) == 2


def test_targeted_retrieval_opens_only_selected_category(tmp_path):
    root = tmp_path / "typed"
    writer = SQLiteMemoryStore(root, toolchain_version="test")
    writer.record_repair_attempt(_attempt(passed=True, after_mismatches=0))
    writer.record_error_observation(
        task_id="task_fail", candidate_id="cand_01", error_signature="FUNCTIONAL_MISMATCH",
        module_family="counter", source_problem_id=None, summary="Observed failure",
        evidence_path=None,
    )
    reader = SQLiteMemoryStore(root, toolchain_version="test")
    found = reader.retrieve(
        error_signature="FUNCTIONAL_MISMATCH", module_family="counter",
        exclude_problem_id="another_problem", memory_types=("REPAIR_STRATEGY",),
    )
    assert len(found) == 1
    assert set(reader._stores) == {"REPAIR_STRATEGY"}
    assert reader.retrieve_design_patterns(module_family="counter", exclude_problem_id=None) == []


def test_scoped_strategy_is_not_a_generator_or_unrelated_error_memory(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "typed", toolchain_version="test")
    item = store.record_repair_attempt(_attempt(passed=True, after_mismatches=0))
    scoped = replace(
        item, memory_id="scoped_timeout", error_signature="TIMEOUT Icarus",
        module_family="rs_kes_bm", source_problem_id=None,
        provenance={"applicable_failure_categories": ["TIMEOUT", "SIMULATION_TIMEOUT"]},
    )
    store._upsert_memory(scoped)
    assert store.retrieve_design_patterns(module_family="rs_kes_bm", exclude_problem_id=None) == []
    assert store.retrieve(error_signature="SYNTAX_ERROR", module_family="rs_kes_bm",
                          exclude_problem_id=None) == []
    assert len(store.retrieve(error_signature="TIMEOUT", module_family="rs_kes_bm",
                              exclude_problem_id=None)) == 1
    store.record_use([scoped.memory_id], positive=False)
    store.record_use([scoped.memory_id], positive=False)
    assert store.retrieve(error_signature="TIMEOUT", module_family="rs_kes_bm",
                          exclude_problem_id=None) == []


def test_debugger_selects_a_category_without_querying_other_files(tmp_path):
    from types import SimpleNamespace

    from rtlforge.domain import RTLTaskContext
    from rtlforge.orchestration.policy import DebugAction
    from rtlforge.services.debug_tools import DebugTools

    calls = []

    def retrieve(context, feedback, **kwargs):
        calls.append(kwargs["memory_types"])
        context.metadata["retrieved_memories"] = []

    operations = SimpleNamespace(
        _retrieve_memories=retrieve, _selected_debugger_feedback=lambda _: {},
    )
    tools = DebugTools(operations, tmp_path)
    context = RTLTaskContext("task", "RS")
    tools.call(DebugAction.RETRIEVE_REPAIR_STRATEGIES, context)
    tools.call(DebugAction.RETRIEVE_ERROR_PATTERNS, context)
    tools.call(DebugAction.RETRIEVE_PROJECT_RULES, context)
    assert calls == [("REPAIR_STRATEGY",), ("ERROR_PATTERN",), ("PROJECT",)]
