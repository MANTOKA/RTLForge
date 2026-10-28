from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from rtlforge.web.views import classify, safe_data, task_view


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def _recorded_time(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _task_time(summary: dict | None, directory: Path) -> tuple[str | float, str]:
    events = (summary or {}).get("events") or []
    timestamps = [_recorded_time(event.get("timestamp")) for event in events
                  if isinstance(event, dict)]
    recorded = [item for item in timestamps if item is not None]
    if recorded:
        return recorded[0], "event"
    return directory.stat().st_mtime, "file_date"


class ArtifactIndex:
    """Read existing evidence without moving or rewriting it."""

    def __init__(self, repo: Path, runs_root: Path | None = None) -> None:
        self.repo = repo.resolve()
        self.root = (runs_root or repo / "runs").resolve()
        if not self.root.is_relative_to(self.repo):
            raise ValueError("runs root must be inside repository")
        self.tasks: dict[str, Path] = {}
        self.reports: dict[str, Path] = {}
        self.artifacts: dict[str, Path] = {}
        self._scan_lock = threading.Lock()
        self._scan_time = 0.0
        self._scan_result: dict | None = None

    @staticmethod
    def _id(path: Path, root: Path) -> str:
        return hashlib.sha256(str(path.relative_to(root)).encode("utf-8")).hexdigest()[:16]

    def scan(self, *, force: bool = False) -> dict:
        with self._scan_lock:
            if (not force and self._scan_result is not None
                    and time.monotonic() - self._scan_time < 2):
                return self._scan_result
            return self._scan_once()

    def _scan_once(self) -> dict:
        task_index: dict[str, Path] = {}
        report_index: dict[str, Path] = {}
        tasks = []
        reports = []
        if not self.root.exists():
            return {"tasks": tasks, "benchmarks": reports}
        task_dirs = {p.parent for p in self.root.rglob("showcase_summary.json")
                     if _inside(p, self.root)}
        task_dirs.update(p.parent for p in self.root.rglob("input.txt")
                         if p.parent.name.startswith("task_") and _inside(p, self.root))
        for directory in sorted(task_dirs, key=lambda p: p.stat().st_mtime, reverse=True):
            path = directory / "showcase_summary.json"
            task_id = directory.name
            if task_id in task_index:
                task_id += "_" + self._id(directory, self.root)[:6]
            task_index[task_id] = directory
            state, summary, error = "partial", None, None
            if path.is_file():
                try:
                    summary = _load_json(path)
                    state = ("current" if all(key in summary for key in
                             ("sampling", "model_calls", "candidate_policy")) else "legacy")
                except (OSError, ValueError) as exc:
                    state, error = "unreadable", type(exc).__name__
            group = (directory.parent.parent if directory.parent.name == "tasks"
                     else directory.parent)
            run_time, time_source = _task_time(summary, directory)
            tasks.append({"id": task_id, "kind": "task", "dataset": group.parent.name,
                          "model": group.name if directory.parent.name == "tasks" else None,
                          "status": summary.get("current_stage") if summary else "PARTIAL",
                          "completeness": state, "error": error,
                          "run_time": run_time, "time_source": time_source,
                          "updated_at": directory.stat().st_mtime})
        for path in sorted(self.root.rglob("*.json"), key=lambda p: p.stat().st_mtime,
                           reverse=True):
            if path.parent.name != "reports" or not _inside(path, self.root):
                continue
            report_id = self._id(path, self.root)
            report_index[report_id] = path
            try:
                data = _load_json(path)
                if not isinstance(data.get("results"), list):
                    continue
                config = data.get("configuration") or {}
                recorded = _recorded_time(data.get("created_at"))
                reports.append({"id": report_id, "kind": "benchmark",
                                "dataset": data.get("dataset"), "model": config.get("model"),
                                "completed": len(data["results"]),
                                "total": config.get("requested_case_count"),
                                "status": "HISTORICAL", "completeness": "current" if
                                "prompt_version" in config else "legacy",
                                "run_time": recorded or path.stat().st_mtime,
                                "time_source": "report_created" if recorded else "file_date",
                                "updated_at": path.stat().st_mtime})
            except (OSError, ValueError):
                reports.append({"id": report_id, "kind": "benchmark", "dataset": None,
                                "model": None, "status": "UNREADABLE",
                                "completeness": "unreadable",
                                "run_time": path.stat().st_mtime, "time_source": "file_date",
                                "updated_at": path.stat().st_mtime})
        self.tasks = task_index
        self.reports = report_index
        self._scan_result = {"tasks": tasks, "benchmarks": reports}
        self._scan_time = time.monotonic()
        return self._scan_result

    def _register_artifact(self, path: Path, task_dir: Path, candidate_id: str) -> dict | None:
        resolved = (self.repo / path).resolve() if not path.is_absolute() else path.resolve()
        if (not _inside(resolved, task_dir / "candidates") or
                resolved.suffix.lower() not in {".v", ".sv"} or not resolved.is_file()):
            return None
        artifact_id = self._id(resolved, self.root)
        self.artifacts[artifact_id] = resolved
        return {"id": artifact_id, "name": resolved.name, "candidate_id": candidate_id}

    def _events_from_sqlite(self, directory: Path, task_id: str) -> list[dict]:
        group = directory.parent.parent if directory.parent.name == "tasks" else directory.parent
        state = group / "state"
        if not state.is_dir():
            state = self.root / "web" / "state"
        if not state.is_dir():
            state = self.repo / "work"
        for db in state.glob("*.sqlite"):
            if not _inside(db, self.repo):
                continue
            try:
                connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
                connection.row_factory = sqlite3.Row
                with connection:
                    rows = connection.execute(
                        "SELECT sequence,event_type,stage,timestamp,payload_json FROM events "
                        "WHERE task_id=? ORDER BY sequence", (task_id,),
                    ).fetchall()
                connection.close()
                if rows:
                    return [{"sequence": row["sequence"], "event_type": row["event_type"],
                             "stage": row["stage"], "timestamp": row["timestamp"],
                             "payload": safe_data(json.loads(row["payload_json"]))} for row in rows]
            except (sqlite3.Error, ValueError, OSError):
                continue
        return []

    def task(self, task_id: str) -> dict:
        directory = self.tasks.get(task_id)
        if directory is None:
            self.scan(force=True)
            directory = self.tasks.get(task_id)
        if directory is None:
            raise KeyError("task not found")
        path = directory / "showcase_summary.json"
        summary, error = None, None
        if path.is_file():
            try:
                summary = _load_json(path)
            except (OSError, ValueError) as exc:
                error = f"摘要不可读取：{type(exc).__name__}"
        state = ("unreadable" if error else "partial" if summary is None else
                 "current" if all(k in summary for k in
                 ("sampling", "model_calls", "candidate_policy")) else "legacy")
        actual_id = directory.name
        events = (summary.get("events") or []) if summary else []
        if not events:
            events = self._events_from_sqlite(directory, actual_id)
        calls = summary.get("model_calls", []) if summary else []
        if not calls and (directory / "model_calls.jsonl").is_file():
            for line in (directory / "model_calls.jsonl").read_text(encoding="utf-8").splitlines():
                try:
                    calls.append(json.loads(line))
                except ValueError:
                    continue
        artifacts = []
        for candidate in (summary or {}).get("candidates") or []:
            value = candidate.get("artifact_path")
            if value:
                artifact = self._register_artifact(Path(value), directory,
                                                   candidate.get("candidate_id"))
                if artifact:
                    artifacts.append(artifact)
        return task_view(summary, task_id=task_id, events=events, calls=calls,
                         artifacts=artifacts,
                         status=(summary.get("current_stage") if summary else
                                 events[-1].get("stage") if events else "PARTIAL"),
                         completeness=state, error=error)

    def benchmark(self, report_id: str) -> dict:
        path = self.reports.get(report_id)
        if path is None:
            self.scan(force=True)
            path = self.reports.get(report_id)
        if path is None:
            raise KeyError("report not found")
        report = _load_json(path)
        if not isinstance(report.get("results"), list):
            raise ValueError("invalid report")
        results = []
        for row in report["results"]:
            result = {key: safe_data(row.get(key)) for key in (
                "problem_id", "status", "failure_category", "task_id",
                "selected_candidate_id", "candidate_count", "repair_attempts",
                "verification_cycles", "passed_after_repair", "timings_ms",
                "token_usage", "model_calls", "summary_path",
            ) if key in row}
            reason = str(row.get("reason") or "")
            memory = row.get("memory") or {}
            result["memory_hits"] = (
                len(memory.get("retrieved") or [])
                if isinstance(memory, dict) and "retrieved" in memory else None
            )
            timings = row.get("timings_ms")
            result["duration_ms"] = (sum(value for value in timings.values()
                                         if isinstance(value, (int, float)))
                                     if isinstance(timings, dict) and timings else None)
            if "AllocationQuota.FreeTierOnly" in reason or "quota exhausted" in reason.lower():
                result["error_kind"] = "QUOTA_EXHAUSTED"
            elif "timeout" in reason.lower() and row.get("status") == "ERROR":
                result["error_kind"] = "TIMEOUT"
            elif row.get("status") == "ERROR":
                result["error_kind"] = "OTHER_ERROR"
            summary_path = row.get("summary_path")
            if summary_path:
                candidate = (self.repo / summary_path).resolve()
                if _inside(candidate, self.root) and candidate.name == "showcase_summary.json":
                    try:
                        summary = _load_json(candidate)
                        result["rescue"] = classify(summary)
                        selected_static = summary.get("selected_static_evaluation") or {}
                        result["static_score"] = selected_static.get("quality_score")
                        result["static_gate"] = selected_static.get("eligible_for_verification")
                    except (OSError, ValueError):
                        result["rescue"] = "UNKNOWN_LEGACY"
            results.append(result)
        statuses = {kind: sum(row.get("status") == kind for row in results)
                    for kind in ("PASS", "FAIL", "ERROR", "SKIPPED_REFERENCE")}
        evaluable = statuses["PASS"] + statuses["FAIL"]
        original_metrics = safe_data(report.get("metrics") or {})
        metrics = {**original_metrics, "evaluable": evaluable,
                   "passed": statuses["PASS"], "failed": statuses["FAIL"],
                   "agent_pass_rate_evaluable":
                   statuses["PASS"] / evaluable if evaluable else None,
                   "status_counts": statuses,
                   "quota_errors": sum(row.get("error_kind") == "QUOTA_EXHAUSTED"
                                       for row in results)}
        return {"id": report_id, "dataset": report.get("dataset"),
                "protocol": report.get("protocol"),
                "configuration": safe_data(report.get("configuration") or {}),
                "metrics": metrics, "stored_metrics": original_metrics, "results": results,
                "last_error": ({"category": report["last_error"].get("category")}
                               if isinstance(report.get("last_error"), dict) else None),
                "created_at": report.get("created_at"),
                "updated_at": report.get("updated_at")}

    def artifact(self, artifact_id: str) -> tuple[str, bytes]:
        path = self.artifacts.get(artifact_id)
        if path is None or not _inside(path, self.root) or not path.is_file():
            raise KeyError("artifact not found")
        return path.name, path.read_bytes()
