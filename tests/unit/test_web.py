from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from rtlforge.web.artifacts import ArtifactIndex
from rtlforge.web.comparison import compare
from rtlforge.web.server import Handler, WebError, Workbench
from rtlforge.web.views import classify


def _fixture(tmp_path):
    repo = tmp_path / "repo"
    task = repo / "runs" / "human-v2" / "model" / "tasks" / "task_fixture"
    task.mkdir(parents=True)
    (task / "input.txt").write_text("counter", encoding="utf-8")
    return repo, task


@pytest.mark.parametrize(("selected", "parent", "passed", "category", "expected"), [
    ("cand_01", None, True, "PASS", "RANK1_PASS"),
    ("cand_02", None, True, "PASS", "FALLBACK_RESCUE"),
    ("cand_01_fix", "cand_01", True, "PASS", "REPAIR_RESCUE"),
    ("cand_02", None, False, "FUNCTIONAL_MISMATCH", "UNRESOLVED"),
    ("cand_02", None, False, "SYSTEM_ERROR", "INFRASTRUCTURE_ERROR"),
])
def test_rescue_classification(selected, parent, passed, category, expected):
    summary = {"current_stage": "COMPLETED" if passed else "FAILED",
               "selected_candidate_id": selected,
               "candidates": [{"candidate_id": "cand_01", "parent_id": None},
                              {"candidate_id": selected, "parent_id": parent}],
               "verification": {"passed": passed, "category": category}}
    assert classify(summary) == expected
    assert classify(None) == "UNKNOWN_LEGACY"


def test_static_only_is_unverified():
    assert classify({"current_stage": "COMPLETED", "verification": None}) == "UNVERIFIED"


def test_index_current_legacy_partial_unreadable_and_oracle_filter(tmp_path):
    repo, task = _fixture(tmp_path)
    candidate = task / "candidates" / "cand_01" / "TopModule.v"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("module TopModule; endmodule", encoding="utf-8")
    payload = {"task_id": "task_fixture", "current_stage": "COMPLETED",
               "input": "counter", "sampling": {"temperature": 0.1},
               "model_calls": [{"input_tokens": None, "role": "planner"}],
               "candidate_policy": "sequential_repair_first_v1",
               "verification": {"passed": True, "category": "PASS",
                                "testbench": "GOLDEN_SECRET"},
               "events": [{"sequence": 1, "event_type": "EVENT_PLAN_DONE",
                           "timestamp": "2026-09-07T16:32:45+00:00",
                           "stage": "PLANNED", "payload": {"golden_source": "GOLDEN_SECRET"}}],
               "candidates": [{"candidate_id": "cand_01", "parent_id": None,
                               "artifact_path": str(candidate), "metadata": {}}],
               "selected_candidate_id": "cand_01"}
    (task / "showcase_summary.json").write_text(json.dumps(payload), encoding="utf-8")
    legacy = task.parent / "task_legacy"
    legacy.mkdir()
    (legacy / "showcase_summary.json").write_text(
        json.dumps({"task_id": "task_legacy", "current_stage": "FAILED"}), encoding="utf-8")
    partial = task.parent / "task_partial"
    partial.mkdir()
    (partial / "input.txt").write_text("x", encoding="utf-8")
    broken = task.parent / "task_broken"
    broken.mkdir()
    (broken / "showcase_summary.json").write_text("{not-json", encoding="utf-8")
    index = ArtifactIndex(repo)
    rows = {row["id"]: row for row in index.scan()["tasks"]}
    assert rows["task_fixture"]["completeness"] == "current"
    assert rows["task_fixture"]["run_time"] == "2026-09-07T16:32:45+00:00"
    assert rows["task_fixture"]["time_source"] == "event"
    assert rows["task_legacy"]["completeness"] == "legacy"
    assert rows["task_partial"]["completeness"] == "partial"
    assert rows["task_partial"]["time_source"] == "file_date"
    assert isinstance(rows["task_partial"]["run_time"], float)
    assert rows["task_broken"]["completeness"] == "unreadable"
    view = index.task("task_fixture")
    assert view["result_type"] == "RANK1_PASS"
    assert view["token_usage"] is not None
    assert "GOLDEN_SECRET" not in json.dumps(view)
    artifact = view["artifacts"][0]
    assert index.artifact(artifact["id"])[1] == b"module TopModule; endmodule"
    with pytest.raises(KeyError):
        index.artifact("ffffffffffffffff")
    assert index.task("task_legacy")["fields"]["sampling"]["availability"] == "not_recorded"


def test_comparison_uses_common_evaluable_subset():
    base = {"id": "a", "protocol": "oracle_assisted_closed_loop",
            "configuration": {"model": "m", "requested_case_count": 4},
            "results": [{"problem_id": "x", "status": "PASS"},
                        {"problem_id": "y", "status": "FAIL"},
                        {"problem_id": "z", "status": "ERROR"},
                        {"problem_id": "only-a", "status": "PASS"}]}
    other = {"id": "b", "protocol": base["protocol"],
             "configuration": {"model": "m", "requested_case_count": 4},
             "results": [{"problem_id": "x", "status": "FAIL"},
                         {"problem_id": "y", "status": "PASS"},
                         {"problem_id": "z", "status": "PASS"},
                         {"problem_id": "only-b", "status": "FAIL"}]}
    result = compare(base, other)
    assert result["scope"]["common_evaluable"] == 2
    assert result["scope"]["excluded_common"] == ["z"]
    assert result["counts"] == {"pass_to_fail": 1, "fail_to_pass": 1, "unchanged": 0}
    assert result["scope"]["only_a"] == ["only-a"]


def test_historical_report_keeps_original_metrics_but_excludes_quota_errors(tmp_path):
    repo, _ = _fixture(tmp_path)
    reports = repo / "runs" / "human-v2" / "model" / "reports"
    reports.mkdir()
    path = reports / "batch.json"
    path.write_text(json.dumps({
        "dataset": "Human v2", "created_at": "2026-09-09T07:13:36+00:00",
        "configuration": {"requested_case_count": 4},
        "metrics": {"evaluable": 4, "passed": 1, "agent_pass_rate_evaluable": 0.25},
        "results": [{"problem_id": "a", "status": "PASS"},
                    {"problem_id": "b", "status": "FAIL"},
                    {"problem_id": "c", "status": "ERROR",
                     "reason": "AllocationQuota.FreeTierOnly GOLDEN_SECRET"},
                    {"problem_id": "d", "status": "SKIPPED_REFERENCE"}],
    }), encoding="utf-8")
    index = ArtifactIndex(repo)
    report_id = index.scan()["benchmarks"][0]["id"]
    assert index.scan()["benchmarks"][0]["run_time"] == "2026-09-09T07:13:36+00:00"
    assert index.scan()["benchmarks"][0]["time_source"] == "report_created"
    view = index.benchmark(report_id)
    assert view["stored_metrics"]["agent_pass_rate_evaluable"] == 0.25
    assert view["metrics"]["evaluable"] == 2
    assert view["metrics"]["agent_pass_rate_evaluable"] == 0.5
    assert view["metrics"]["quota_errors"] == 1
    assert "GOLDEN_SECRET" not in json.dumps(view)


def test_task_parameter_allowlist_and_recovery(tmp_path, monkeypatch):
    repo, _ = _fixture(tmp_path)
    jobs = repo / "runs" / "web" / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "run_old.json").write_text(
        json.dumps({"id": "run_old", "kind": "benchmark", "status": "RUNNING",
                    "created_at": "2026-10-08T05:19:07+00:00"}),
        encoding="utf-8")
    workbench = Workbench(repo)
    assert workbench.jobs["run_old"]["status"] == "INTERRUPTED"
    recovered = next(row for row in workbench.history()["benchmarks"]
                     if row["id"] == "run_old")
    assert recovered["run_time"] == "2026-10-08T05:19:07+00:00"
    (repo / ".env").write_text("RTLFORGE_MODEL=test-model\nOPENAI_API_KEY=test\n",
                              encoding="utf-8")
    monkeypatch.setattr(workbench, "settings", lambda profile=".env": {
        "configured": True, "model": "test-model"})
    monkeypatch.setattr(workbench, "docker_status", lambda: {"available": True})
    captured = {}
    def launch(state, argv):
        captured.update(state=state, argv=argv)
        return {"id": state["id"]}
    monkeypatch.setattr(workbench, "_launch", launch)
    with pytest.raises(WebError, match="请求含不支持"):
        workbench.start_task({"source": "natural", "spec": "x", "command": "danger"})
    with pytest.raises(WebError):
        workbench.start_task({"source": "natural", "spec": "x", "level": "verify",
                              "testbench_id": "../oracle.sv"})
    workbench.start_task({"source": "natural", "spec": "counter", "level": "static"})
    assert "--task-id" in captured["argv"]
    assert captured["argv"][0] == "rtlforge.benchmarks.single_case_demo"
    assert captured["state"]["id"].startswith("task_")
    assert captured["argv"][captured["argv"].index("--memory-mode") + 1] == "read_write"


def test_benchmark_resume_reuses_config_and_rejects_changes(tmp_path, monkeypatch):
    repo, _ = _fixture(tmp_path)
    workbench = Workbench(repo)
    (repo / ".env").write_text("RTLFORGE_MODEL=test-model\nOPENAI_API_KEY=test\n",
                              encoding="utf-8")
    monkeypatch.setattr(workbench, "settings", lambda profile=".env": {
        "configured": True, "model": "test-model"})
    monkeypatch.setattr(workbench, "docker_status", lambda: {"available": True})
    monkeypatch.setattr(workbench, "capabilities", lambda: {"datasets": {
        "human_v2": {"available": True}}})
    output = repo / "runs" / "web" / "reports" / "run_old.json"
    output.parent.mkdir(parents=True)
    output.write_text("{}", encoding="utf-8")
    workbench.jobs["run_old"] = {
        "id": "run_old", "kind": "benchmark", "status": "INTERRUPTED",
        "dataset": "human_v2", "model": "test-model", "limit": 10,
        "provider_profile": ".env",
        "candidates": 2, "max_repairs": 2, "memory_mode": "off",
        "docker_image": "rtlforge-sandbox:reflection", "output": str(output),
    }
    captured = {}
    monkeypatch.setattr(workbench, "_launch", lambda state, argv: captured.update(
        state=state, argv=argv))
    payload = {"dataset": "human_v2", "model": "test-model", "limit": 10,
               "resume_run_id": "run_old", "memory_mode": "off"}
    workbench.start_benchmark(payload)
    assert captured["state"]["output"] == str(output)
    assert "--resume" in captured["argv"]
    with pytest.raises(WebError, match="续跑配置必须"):
        workbench.start_benchmark({**payload, "memory_mode": "read_write"})


def test_provider_profiles_change_endpoint_and_hide_keys(tmp_path, monkeypatch):
    repo, _ = _fixture(tmp_path)
    (repo / ".env").write_text("OPENAI_API_KEY=SECRET_A\nRTLFORGE_MODEL=model-a\n"
                              "OPENAI_BASE_URL=https://a.example/v1\n", encoding="utf-8")
    (repo / ".env.qwen").write_text("OPENAI_API_KEY=SECRET_B\n"
                                   "RTLFORGE_MODEL=qwen3-coder-plus\n"
                                   "OPENAI_BASE_URL=https://b.example/v1\n", encoding="utf-8")
    workbench = Workbench(repo)
    monkeypatch.setattr(workbench, "docker_status", lambda: {"available": True})
    capabilities = workbench.capabilities()
    assert capabilities["profiles"][".env.qwen"]["model"] == "qwen3-coder-plus"
    assert capabilities["profiles"][".env.qwen"]["base_url"] == "https://b.example/v1"
    assert "SECRET_A" not in json.dumps(capabilities)
    assert "SECRET_B" not in json.dumps(capabilities)
    assert workbench._provider_environment(".env.qwen")["OPENAI_API_KEY"] == "SECRET_B"
    with pytest.raises(WebError, match="模型配置不存在"):
        workbench._provider_environment("../private.env")


def test_docker_unavailable_blocks_before_launch(tmp_path, monkeypatch):
    repo, _ = _fixture(tmp_path)
    (repo / ".env").write_text("OPENAI_API_KEY=test\nRTLFORGE_MODEL=test-model\n",
                              encoding="utf-8")
    workbench = Workbench(repo)
    monkeypatch.setattr(workbench, "docker_status", lambda: {"available": False})
    with pytest.raises(WebError) as error:
        workbench.start_task({"source": "natural", "spec": "counter", "level": "static"})
    assert error.value.code == "DOCKER_UNAVAILABLE"


def test_preflight_failure_explains_no_model_call(tmp_path):
    repo, task = _fixture(tmp_path)
    report = task.parent.parent / "single-case" / "Prob155_lemmings4_reference.json"
    report.parent.mkdir()
    report.write_text(json.dumps({"results": [{"status": "FAIL", "category": "SYSTEM_ERROR",
                                              "reason": "could not start Docker: [WinError 5]"}]}),
                      encoding="utf-8")
    workbench = Workbench(repo)
    state = {"kind": "task", "source": "human_v2", "case": "Prob155_lemmings4",
             "task_dir": str(task)}
    assert "WinError 5" in workbench._preflight_failure(state)["message"]
    assert workbench._preflight_failure(state)["agent_calls"] == 0
    report.write_text(json.dumps({"results": [{"status": "FAIL",
                                              "category": "TOOL_INCOMPATIBILITY"}]}),
                      encoding="utf-8")
    assert "EDA 工具链不兼容" in workbench._preflight_failure(state)["message"]


def test_http_history_task_and_artifact_boundaries(tmp_path):
    repo, task = _fixture(tmp_path)
    (task / "showcase_summary.json").write_text(json.dumps({"task_id": "task_fixture",
        "current_stage": "COMPLETED", "verification": {"testbench": "GOLDEN_SECRET"},
        "events": [{"sequence": 1, "event_type": "EVENT_PLAN_DONE", "stage": "PLANNED",
                    "payload": {"reference": "GOLDEN_SECRET"}}]}), encoding="utf-8")
    Handler.workbench = Workbench(repo)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        for path in ("/api/history", "/api/tasks/task_fixture",
                     "/api/tasks/task_fixture/events", "/api/artifacts/ffffffffffffffff"):
            connection.request("GET", path)
            response = connection.getresponse()
            content = response.read().decode("utf-8")
            assert "GOLDEN_SECRET" not in content
            assert response.status == (404 if "artifacts" in path else 200)
        connection.request("POST", "/api/tasks", body=json.dumps({"command": "evil"}),
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 400
        assert json.loads(response.read())["error"]["code"] == "INVALID_FIELDS"
        connection.close()
    finally:
        server.shutdown()
        server.server_close()


def test_sse_event_ids_and_reconnect(tmp_path):
    repo, task = _fixture(tmp_path)
    (task / "showcase_summary.json").write_text(json.dumps({
        "task_id": "task_fixture", "current_stage": "COMPLETED",
        "events": [{"sequence": 1, "event_type": "EVENT_TASK_CREATED", "stage": "CREATED"},
                   {"sequence": 2, "event_type": "EVENT_PLAN_DONE", "stage": "PLANNED"}],
    }), encoding="utf-8")
    workbench = Workbench(repo)
    workbench.jobs["task_fixture"] = {"id": "task_fixture", "kind": "task",
                                      "status": "COMPLETED", "task_dir": str(task)}
    Handler.workbench = workbench
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/api/tasks/task_fixture/stream",
                           headers={"Last-Event-ID": "1"})
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert "id: 1\n" not in body
        assert "id: 2\n" in body and "EVENT_PLAN_DONE" in body
        assert "id: 3\n" in body and "finished" in body
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
