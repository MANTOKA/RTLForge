from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import dotenv_values

from rtlforge.benchmarks.rtllm import load_rtllm, rtllm_run_root
from rtlforge.benchmarks.verilogeval import human_v2_run_root, load_human_v2
from rtlforge.memory import DEFAULT_MEMORY_DATABASE
from rtlforge.providers import ProviderConfigurationError, ProviderSettings
from rtlforge.services.candidate_evaluation import DockerEDARunner, DockerToolError
from rtlforge.web.artifacts import ArtifactIndex
from rtlforge.web.comparison import compare
from rtlforge.web.report import markdown
from rtlforge.web.views import task_view

REPO = Path(__file__).resolve().parents[2]
DATASETS = {
    "human_v2": ("data/benchmarks/verilog-eval-human-v2",
                 "rtlforge.benchmarks.single_case_demo",
                 "rtlforge.benchmarks.human_v2_batch"),
    "rtllm": ("data/benchmarks/rtllm-2.1",
              "rtlforge.benchmarks.rtllm_demo",
              "rtlforge.benchmarks.rtllm_batch"),
}
TASK_FIELDS = {"source", "spec", "dataset", "case", "level", "model", "provider_profile",
               "candidates",
               "max_repairs", "memory_mode", "docker_image", "testbench_id"}
BATCH_FIELDS = {"dataset", "limit", "model", "provider_profile", "candidates",
                "max_repairs", "memory_mode",
                "docker_image", "resume_run_id"}
MEMORY_MODES = {"off", "read_only", "read_write"}
LEVELS = {"generate", "static", "verify"}
PROVIDER_KEYS = {"OPENAI_API_KEY", "OPENAI_BASE_URL", "RTLFORGE_MODEL",
                 "RTLFORGE_API_STYLE",
                 "RTLFORGE_REQUEST_TIMEOUT_SECONDS", "RTLFORGE_MAX_OUTPUT_TOKENS",
                 "RTLFORGE_MAX_RETRIES", "RTLFORGE_TEMPERATURE",
                 "RTLFORGE_TOP_P", "RTLFORGE_SEED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WebError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details=None) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details


def _options(payload: dict, allowed: set[str]) -> None:
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise WebError("INVALID_FIELDS", "请求含不支持的字段")


def _integer(payload: dict, key: str, choices: tuple[int, ...], default: int) -> int:
    value = payload.get(key, default)
    if type(value) is not int or value not in choices:
        raise WebError("INVALID_PARAMETER", f"{key} 不在允许范围内")
    return value


def _text(payload: dict, key: str, default: str, choices: set[str] | None = None) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise WebError("INVALID_PARAMETER", f"{key} 无效")
    if choices is not None and value not in choices:
        raise WebError("INVALID_PARAMETER", f"{key} 不在允许范围内")
    return value


class Workbench:
    def __init__(self, repo: Path = REPO, runs_root: Path | None = None) -> None:
        self.repo = repo.resolve()
        self.index = ArtifactIndex(self.repo, runs_root)
        self.state_dir = self.index.root / "web" / "jobs"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.processes: dict[str, subprocess.Popen] = {}
        self.jobs: dict[str, dict] = {}
        for path in self.state_dir.glob("*.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                if state.get("status") == "RUNNING":
                    state["status"] = "INTERRUPTED"
                    state["message"] = "服务重启后无法确认原进程状态；请查看已保存的产物"
                    self._save(state)
                self.jobs[state["id"]] = state
            except (OSError, ValueError, KeyError):
                continue

    def _save(self, state: dict) -> None:
        destination = self.state_dir / f"{state['id']}.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, destination)

    def _profiles(self) -> dict[str, Path]:
        paths = [self.repo / ".env", *sorted(self.repo.glob(".env.*"))]
        return {path.name: path for path in paths
                if path.is_file() and path.name != ".env.example"
                and re.fullmatch(r"\.env(?:\.[A-Za-z0-9_-]+)?", path.name)}

    def _provider_environment(self, profile: str) -> dict[str, str]:
        path = self._profiles().get(profile)
        if path is None:
            raise WebError("INVALID_PROFILE", "模型配置不存在")
        values = {key: value for key, value in dotenv_values(path).items()
                  if key in PROVIDER_KEYS and value is not None}
        environment = {key: value for key, value in os.environ.items()
                       if key not in PROVIDER_KEYS}
        environment.update(values)
        return environment

    def settings(self, profile: str = ".env") -> dict:
        try:
            settings = ProviderSettings.from_env(self._provider_environment(profile))
            return {"configured": True, "model": settings.model,
                    "base_url": settings.base_url, "sampling": settings.sampling_configuration()}
        except (ProviderConfigurationError, WebError) as exc:
            return {"configured": False, "error": str(exc), "model": None, "sampling": None}

    def capabilities(self) -> dict:
        datasets = {}
        for name, (relative, _, _) in DATASETS.items():
            root = self.repo / relative
            try:
                cases = (load_human_v2(root, expected_count=None) if name == "human_v2"
                         else load_rtllm(root, expected_count=None))
                datasets[name] = {"available": True,
                                  "cases": [item.problem_id for item in cases]}
            except (OSError, ValueError):
                datasets[name] = {"available": False, "cases": []}
        profiles = {name: self.settings(name) for name in self._profiles()}
        return {"provider": profiles.get(".env", self.settings()), "profiles": profiles,
                "datasets": datasets,
                "docker": self.docker_status(),
                "memory_modes": sorted(MEMORY_MODES), "candidate_caps": [1, 2, 3],
                "repair_budgets": [0, 1, 2, 3],
                "protocol": "oracle_assisted_closed_loop"}

    def docker_status(self) -> dict:
        try:
            binary = DockerEDARunner(self.index.root)._docker_binary()
            result = subprocess.run([binary, "info", "--format", "{{.ServerVersion}}"],
                                    capture_output=True, text=True, timeout=4, check=False)
        except (DockerToolError, OSError, subprocess.TimeoutExpired) as exc:
            return {"available": False, "reason": str(exc)}
        return {"available": result.returncode == 0,
                "reason": "Docker 可用" if result.returncode == 0 else
                (result.stderr.strip() or "Docker Desktop 未就绪")[:300]}

    def _validate_common(self, payload: dict) -> tuple[str, str, int, int, str, str]:
        profile = _text(payload, "provider_profile", ".env", set(self._profiles()))
        provider = self.settings(profile)
        if not provider["configured"]:
            raise WebError("PROVIDER_NOT_CONFIGURED", "请先在服务端配置模型与 API key")
        model = _text(payload, "model", provider["model"])
        candidates = _integer(payload, "candidates", (1, 2, 3), 2)
        repairs = _integer(payload, "max_repairs", (0, 1, 2, 3), 2)
        memory = _text(payload, "memory_mode", "read_write", MEMORY_MODES)
        image = _text(payload, "docker_image", "rtlforge-sandbox:reflection")
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", image):
            raise WebError("INVALID_PARAMETER", "docker_image 格式无效")
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", model):
            raise WebError("INVALID_PARAMETER", "model 格式无效")
        return profile, model, candidates, repairs, memory, image

    def _launch(self, state: dict, argv: list[str]) -> dict:
        with self.lock:
            if any(job.get("status") == "RUNNING" for job in self.jobs.values()):
                raise WebError("RUN_BUSY", "已有运行中的任务，请等待完成", 409)
            log_path = self.state_dir / f"{state['id']}.log"
            with log_path.open("wb") as stream:
                environment = self._provider_environment(state["provider_profile"])
                process = subprocess.Popen([sys.executable, "-m", *argv], cwd=self.repo,
                                           stdout=stream, stderr=subprocess.STDOUT,
                                           shell=False, env=environment)
            state.update(status="RUNNING", pid=process.pid, started_at=_now())
            self.jobs[state["id"]] = state
            self.processes[state["id"]] = process
            self._save(state)
            threading.Thread(target=self._watch, args=(state["id"], process),
                             daemon=True).start()
        return {"id": state["id"], "status": "RUNNING"}

    def _watch(self, run_id: str, process: subprocess.Popen) -> None:
        code = process.wait()
        with self.lock:
            state = self.jobs[run_id]
            state.update(status="COMPLETED" if code == 0 else "ERROR",
                         exit_code=code, ended_at=_now())
            if code:
                preflight = self._preflight_failure(state)
                state["message"] = (preflight["message"] if preflight else
                                    "执行失败；请检查服务端运行日志")
            self._save(state)
            self.processes.pop(run_id, None)

    def _preflight_failure(self, state: dict) -> dict | None:
        if state.get("kind") != "task" or state.get("source") not in DATASETS:
            return None
        case = state.get("case")
        if not isinstance(case, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", case):
            return None
        directory = Path(state["task_dir"])
        report = directory.parent.parent / "single-case" / f"{case}_reference.json"
        if not report.resolve().is_relative_to(self.index.root):
            return None
        try:
            result = json.loads(report.read_text(encoding="utf-8"))["results"][0]
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return None
        if result.get("status") == "PASS":
            return None
        reason = str(result.get("reason") or "")
        if "WinError 5" in reason:
            message = "Docker 启动被 Windows 拒绝访问（WinError 5）；模型尚未调用。"
        elif "Docker CLI was not found" in reason:
            message = "未找到 Docker CLI；模型尚未调用。"
        elif "Docker" in reason:
            message = "Docker 预检失败；模型尚未调用。"
        elif result.get("category") == "TOOL_INCOMPATIBILITY":
            message = "官方参考设计与当前 EDA 工具链不兼容；模型尚未调用。"
        else:
            message = "官方参考设计预检未通过；模型尚未调用。"
        return {"category": result.get("category"), "message": message,
                "agent_calls": 0}

    def start_task(self, payload: dict) -> dict:
        _options(payload, TASK_FIELDS)
        source = _text(payload, "source", "natural", {"natural", "human_v2", "rtllm"})
        profile, model, candidates, repairs, memory, image = self._validate_common(payload)
        level = _text(payload, "level", "verify" if source != "natural" else "static", LEVELS)
        if level != "generate" and not self.docker_status()["available"]:
            raise WebError("DOCKER_UNAVAILABLE", "Docker 当前不可用；静态检查和功能验证无法启动")
        task_id = "task_" + uuid4().hex[:16]
        argv = ["rtlforge.benchmarks.rtllm_demo" if source == "rtllm" else
                "rtlforge.benchmarks.single_case_demo"]
        if source == "natural":
            spec = payload.get("spec")
            if not isinstance(spec, str) or not spec.strip() or len(spec) > 20000:
                raise WebError("INVALID_PARAMETER", "请输入不超过 20000 字的需求")
            if repairs and level != "verify":
                repairs = 0
            argv.append(spec)
            testbench_id = payload.get("testbench_id")
            if level == "verify":
                testbench = self._testbench(testbench_id)
                argv.extend(["--testbench", str(testbench)])
        else:
            if level != "verify":
                raise WebError("INVALID_PARAMETER", "基准单题使用完整验证流程")
            dataset = DATASETS[source]
            case = _text(payload, "case", "")
            cases = self.capabilities()["datasets"][source]["cases"]
            if case not in cases:
                raise WebError("CASE_NOT_FOUND", "数据集中没有这道题", 404)
            argv.extend(["--dataset-root" if source == "rtllm" else "--human-v2-root",
                         str(self.repo / dataset[0]),
                         "--case" if source == "rtllm" else "--human-v2-case", case])
            spec = None
        argv.extend(["--env-file", str(self._profiles()[profile]),
                     "--task-id", task_id, "--runs-root", str(self.index.root),
                     "--database", str(self.index.root / "web" / "state" / "showcase.sqlite"),
                     "--memory-database", str(DEFAULT_MEMORY_DATABASE),
                     "--model", model, "--candidates", str(candidates),
                     "--max-repairs", str(repairs), "--memory-mode", memory,
                     "--docker-image", image])
        if level in {"static", "verify"}:
            argv.append("--static-evaluation")
        state = {"id": task_id, "kind": "task", "source": source,
                 "case": payload.get("case"), "input": spec, "model": model,
                 "provider_profile": profile,
                 "level": level, "created_at": _now(),
                 "task_dir": str((rtllm_run_root(self.index.root, model) / "tasks" if
                                  source == "rtllm" else
                                  human_v2_run_root(self.index.root, model) / "tasks" if
                                  source == "human_v2" else self.index.root) / task_id)}
        return self._launch(state, argv)

    def _testbench(self, identifier: object) -> Path:
        root = self.repo / "data" / "benchmarks" / "user-testbenches"
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", identifier):
            raise WebError("TESTBENCH_REQUIRED", "请从受控 testbench 目录选择文件")
        path = (root / identifier).resolve()
        if not path.is_relative_to(root.resolve()) or path.suffix not in {".v", ".sv"}:
            raise WebError("INVALID_PARAMETER", "testbench 路径无效")
        if not path.is_file():
            raise WebError("TESTBENCH_NOT_FOUND", "指定 testbench 不存在", 404)
        return path

    def start_benchmark(self, payload: dict) -> dict:
        _options(payload, BATCH_FIELDS)
        dataset = _text(payload, "dataset", "human_v2", set(DATASETS))
        profile, model, candidates, repairs, memory, image = self._validate_common(payload)
        if not self.capabilities()["datasets"][dataset]["available"]:
            raise WebError("DATASET_NOT_FOUND", "数据集不可用", 404)
        if not self.docker_status()["available"]:
            raise WebError("DOCKER_UNAVAILABLE", "Docker 当前不可用；批量测试无法启动")
        limit = payload.get("limit")
        if limit is not None and (type(limit) is not int or limit < 1 or limit > 156):
            raise WebError("INVALID_PARAMETER", "limit 必须是正整数")
        resume_id = payload.get("resume_run_id")
        run_id = "run_" + uuid4().hex[:16]
        output = self.index.root / "web" / "reports" / f"{run_id}.json"
        if resume_id is not None:
            prior = self.jobs.get(resume_id)
            if not prior or prior.get("kind") != "benchmark" or prior.get("status") == "RUNNING":
                raise WebError("INVALID_RESUME", "没有可续跑的批量记录")
            expected = {"dataset": dataset, "model": model, "limit": limit,
                        "provider_profile": profile,
                        "candidates": candidates, "max_repairs": repairs,
                        "memory_mode": memory, "docker_image": image}
            if any(prior.get(key) != value for key, value in expected.items()):
                raise WebError("INVALID_RESUME", "续跑配置必须与原批次一致")
            output = Path(prior["output"])
            if not output.resolve().is_relative_to(self.index.root) or not output.is_file():
                raise WebError("INVALID_RESUME", "原报告不存在")
        argv = [DATASETS[dataset][2], "--env-file", str(self._profiles()[profile]),
                "--dataset-root", str(self.repo / DATASETS[dataset][0]),
                "--runs-root", str(self.index.root), "--model", model,
                "--candidates", str(candidates), "--max-repairs", str(repairs),
                "--memory-mode", memory, "--docker-image", image,
                "--memory-database", str(DEFAULT_MEMORY_DATABASE),
                "--output", str(output)]
        if limit is not None:
            argv.extend(["--limit", str(limit)])
        if resume_id is not None:
            argv.append("--resume")
        state = {"id": run_id, "kind": "benchmark", "dataset": dataset,
                 "provider_profile": profile,
                 "model": model, "limit": limit, "output": str(output),
                 "candidates": candidates, "max_repairs": repairs,
                 "memory_mode": memory, "docker_image": image,
                 "resume_run_id": resume_id,
                 "created_at": _now()}
        return self._launch(state, argv)

    def task(self, task_id: str) -> dict:
        state = self.jobs.get(task_id)
        if state and state.get("task_dir"):
            directory = Path(state["task_dir"])
            if directory.resolve().is_relative_to(self.index.root):
                self.index.tasks[task_id] = directory
        try:
            view = self.index.task(task_id)
        except KeyError:
            if not state:
                raise
            view = task_view(None, task_id=task_id, events=[], calls=[], artifacts=[],
                             status=state["status"], completeness="partial")
            view["fields"]["input"] = {"value": state.get("input"),
                                       "availability": "recorded" if state.get("input") else
                                       "not_recorded", "source": "web_job.input"}
        if state:
            preflight = self._preflight_failure(state)
            if preflight:
                view["preflight"] = preflight
                view["result_type"] = "INFRASTRUCTURE_ERROR"
                view["summary_markdown"] = (
                    f"结果：基础设施错误（{preflight['category']}）\n"
                    f"原因：{preflight['message']}\n"
                    "模型调用：0；Planner、Generator、Debugger 均未运行。\n"
                )
                if view["fields"]["input"]["availability"] != "recorded":
                    view["fields"]["input"] = {
                        "value": f"基准题 {state.get('case')}（参考预检失败，未进入 Agent）",
                        "availability": "recorded", "source": "web_job.case"}
            view["run"] = {key: value for key, value in state.items()
                           if key not in {"input", "output", "pid", "task_dir"}}
        return view

    def benchmark(self, run_id: str) -> dict:
        state = self.jobs.get(run_id)
        if state and state.get("kind") == "benchmark":
            output = Path(state["output"])
            if output.is_file():
                report_id = self.index._id(output, self.index.root)
                self.index.reports[report_id] = output
                view = self.index.benchmark(report_id)
            else:
                view = {"id": run_id, "dataset": state["dataset"], "configuration": {},
                        "metrics": {}, "results": []}
            view["run"] = {key: value for key, value in state.items()
                           if key not in {"output", "pid"}}
            return view
        return self.index.benchmark(run_id)

    def history(self) -> dict:
        scanned = self.index.scan()
        entries = {"tasks": list(scanned["tasks"]), "benchmarks": list(scanned["benchmarks"])}
        for state in self.jobs.values():
            items = entries["tasks" if state["kind"] == "task" else "benchmarks"]
            existing = next((item for item in items if item["id"] == state["id"]), None)
            if existing is not None and existing.get("time_source") == "file_date":
                replacement = {**existing, "run_time": state.get("started_at") or
                               state.get("created_at"), "time_source": "job_started"}
                items[items.index(existing)] = replacement
            elif existing is None:
                items.insert(0, {"id": state["id"], "kind": state["kind"],
                                 "dataset": state.get("dataset") or state.get("source"),
                                 "model": state.get("model"), "status": state["status"],
                                 "completeness": "partial",
                                 "run_time": state.get("started_at") or state.get("created_at"),
                                 "time_source": "job_started",
                                 "updated_at": state.get("started_at")})
        return entries


class Handler(BaseHTTPRequestHandler):
    workbench: Workbench
    protocol_version = "HTTP/1.1"

    def log_message(self, format_string: str, *args) -> None:
        # Access logs include only the route; never log submitted requirements or credentials.
        if self.path.startswith("/api/"):
            return
        super().log_message(format_string, *args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                         "style-src 'self'; connect-src 'self'; object-src 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value: object, status: int = 200) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, error: WebError) -> None:
        self._json({"error": {"code": error.code, "message": error.message,
                              "details": error.details}}, error.status)

    def _body(self) -> dict:
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise WebError("INVALID_CONTENT_TYPE", "请发送 JSON 请求")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise WebError("INVALID_BODY", "请求体大小无效") from exc
        if length <= 0 or length > 65536:
            raise WebError("INVALID_BODY", "请求体大小无效")
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError as exc:
            raise WebError("INVALID_JSON", "JSON 无法解析") from exc
        if not isinstance(data, dict):
            raise WebError("INVALID_BODY", "请求体必须是对象")
        return data

    def _origin(self) -> None:
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://127.0.0.1:" + str(self.server.server_port),
                                     "http://localhost:" + str(self.server.server_port)}:
            raise WebError("FORBIDDEN_ORIGIN", "只允许本地页面发起请求", 403)

    def do_POST(self) -> None:
        try:
            self._origin()
            path = urlsplit(self.path).path
            payload = self._body()
            if path == "/api/tasks":
                return self._json(self.workbench.start_task(payload), 201)
            if path == "/api/benchmarks":
                return self._json(self.workbench.start_benchmark(payload), 201)
            if path == "/api/comparisons":
                _options(payload, {"a_id", "b_id"})
                a, b = payload.get("a_id"), payload.get("b_id")
                if not isinstance(a, str) or not isinstance(b, str) or a == b:
                    raise WebError("INVALID_COMPARISON", "请选择两份不同报告")
                return self._json(compare(self.workbench.benchmark(a),
                                          self.workbench.benchmark(b)))
            raise WebError("NOT_FOUND", "接口不存在", 404)
        except WebError as exc:
            self._error(exc)
        except (KeyError, ValueError):
            self._error(WebError("NOT_FOUND", "记录不存在或不可读取", 404))

    def do_GET(self) -> None:
        try:
            path = urlsplit(self.path).path
            if path == "/api/health":
                return self._json({"status": "ok", "time": _now()})
            if path == "/api/capabilities":
                return self._json(self.workbench.capabilities())
            if path == "/api/history":
                return self._json(self.workbench.history())
            if path == "/api/benchmarks":
                return self._json(self.workbench.history()["benchmarks"])
            if path == "/api/openapi.json":
                return self._json(_openapi())
            match = re.fullmatch(
                r"/api/tasks/([A-Za-z0-9_-]+)(?:/(events|stream|summary.md))?", path,
            )
            if match:
                task_id, action = match.groups()
                if action == "stream":
                    return self._stream("task", task_id)
                view = self.workbench.task(task_id)
                if action == "events":
                    return self._json(view["events"])
                if action == "summary.md":
                    return self._send(200, view["summary_markdown"].encode("utf-8"),
                                      "text/markdown; charset=utf-8")
                return self._json(view)
            match = re.fullmatch(r"/api/benchmarks/([A-Za-z0-9_-]+)(?:/(stream|report.md))?", path)
            if match:
                report_id, action = match.groups()
                if action == "stream":
                    return self._stream("benchmark", report_id)
                view = self.workbench.benchmark(report_id)
                if action == "report.md":
                    return self._send(200, markdown(view).encode("utf-8"),
                                      "text/markdown; charset=utf-8")
                return self._json(view)
            match = re.fullmatch(r"/api/artifacts/([a-f0-9]{16})", path)
            if match:
                name, content = self.workbench.index.artifact(match.group(1))
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(content)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                return self.wfile.write(content)
            static = {"/": "index.html", "/index.html": "index.html",
                      "/app.js": "app.js", "/style.css": "style.css"}.get(path)
            if re.fullmatch(
                r"/(tasks/(new|[A-Za-z0-9_-]+)|benchmarks/(new|[A-Za-z0-9_-]+)"
                r"|history|compare|settings)", path,
            ):
                static = "index.html"
            if static:
                file_path = Path(__file__).parent / "static" / static
                content_type = ("text/javascript" if static.endswith(".js") else
                                "text/css" if static.endswith(".css") else "text/html")
                return self._send(200, file_path.read_bytes(), content_type + "; charset=utf-8")
            raise WebError("NOT_FOUND", "页面不存在", 404)
        except WebError as exc:
            self._error(exc)
        except (KeyError, ValueError):
            self._error(WebError("NOT_FOUND", "记录不存在或不可读取", 404))
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _stream(self, kind: str, run_id: str) -> None:
        try:
            after = int(self.headers.get("Last-Event-ID", "0"))
        except ValueError:
            after = 0
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for _ in range(30):
                view = (self.workbench.task(run_id) if kind == "task" else
                        self.workbench.benchmark(run_id))
                if kind == "task":
                    events = [{"id": int(e.get("sequence") or i + 1), "type": "stage",
                               "task_id": run_id, "timestamp": e.get("timestamp"),
                               "payload": e} for i, e in enumerate(view["events"])]
                else:
                    events = [{"id": i + 1, "type": "result", "run_id": run_id,
                               "timestamp": view.get("updated_at"),
                               "payload": {"problem_id": row.get("problem_id"),
                                           "status": row.get("status")}}
                              for i, row in enumerate(view.get("results") or [])]
                state = view.get("run") or {}
                if state.get("status") in {"COMPLETED", "ERROR", "INTERRUPTED"}:
                    events.append({"id": len(events) + 1, "type": "finished",
                                   "timestamp": state.get("ended_at"),
                                   "payload": {"status": state["status"]}})
                for event in events:
                    if event["id"] <= after:
                        continue
                    body = (f"id: {event['id']}\nevent: {event['type']}\n"
                            f"data: {json.dumps(event, ensure_ascii=False)}\n\n").encode()
                    self.wfile.write(body)
                    self.wfile.flush()
                    after = event["id"]
                if state.get("status") in {"COMPLETED", "ERROR", "INTERRUPTED"}:
                    break
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


class LocalHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        if sys.exc_info()[0] in {BrokenPipeError, ConnectionAbortedError,
                                 ConnectionResetError}:
            return
        super().handle_error(request, client_address)


def _openapi() -> dict:
    string = {"type": "string"}
    common = {
        "model": string, "provider_profile": string,
        "candidates": {"type": "integer", "enum": [1, 2, 3]},
        "max_repairs": {"type": "integer", "enum": [0, 1, 2, 3]},
        "memory_mode": {"type": "string", "enum": sorted(MEMORY_MODES)},
        "docker_image": string,
    }
    schemas = {
        "TaskRequest": {"type": "object", "additionalProperties": False,
                        "properties": {**common, "source": {"type": "string", "enum":
                                      ["natural", "human_v2", "rtllm"]},
                                       "spec": string, "case": string,
                                       "level": {"type": "string", "enum": sorted(LEVELS)},
                                       "testbench_id": string}},
        "BenchmarkRequest": {"type": "object", "additionalProperties": False,
                             "properties": {**common, "dataset": {"type": "string", "enum":
                                            list(DATASETS)},
                                            "limit": {"type": "integer", "minimum": 1},
                                            "resume_run_id": string}},
        "ComparisonRequest": {"type": "object", "additionalProperties": False,
                              "required": ["a_id", "b_id"],
                              "properties": {"a_id": string, "b_id": string}},
        "Error": {"type": "object", "required": ["error"],
                  "properties": {"error": {"type": "object", "properties": {
                      "code": string, "message": string, "details": {}}}}},
    }
    def operation(method: str, schema: str | None = None) -> dict:
        result = {"responses": {"200": {"description": "OK"},
                                "400": {"description": "Invalid request",
                                        "content": {"application/json": {"schema": {
                                            "$ref": "#/components/schemas/Error"}}}}}}
        if method == "post":
            result["requestBody"] = {"required": True, "content": {"application/json": {
                "schema": {"$ref": f"#/components/schemas/{schema}"}}}}
        return result
    paths = {
        "/api/health": {"get": operation("get")},
        "/api/capabilities": {"get": operation("get")},
        "/api/history": {"get": operation("get")},
        "/api/tasks": {"post": operation("post", "TaskRequest")},
        "/api/tasks/{task_id}": {"get": operation("get")},
        "/api/tasks/{task_id}/events": {"get": operation("get")},
        "/api/tasks/{task_id}/stream": {"get": operation("get")},
        "/api/benchmarks": {"get": operation("get"),
                            "post": operation("post", "BenchmarkRequest")},
        "/api/benchmarks/{run_id}": {"get": operation("get")},
        "/api/benchmarks/{run_id}/stream": {"get": operation("get")},
        "/api/comparisons": {"post": operation("post", "ComparisonRequest")},
        "/api/artifacts/{artifact_id}": {"get": operation("get")},
    }
    for path, methods in paths.items():
        names = re.findall(r"\{([^}]+)\}", path)
        for detail in methods.values():
            if names:
                detail["parameters"] = [
                    {"name": name, "in": "path", "required": True, "schema": string}
                    for name in names
                ]
    return {"openapi": "3.0.3", "info": {"title": "RTLForge local workbench",
                                           "version": "0.1.0"},
            "components": {"schemas": schemas}, "paths": paths}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local RTLForge workbench")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    Handler.workbench = Workbench()
    server = LocalHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"RTLForge workbench: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
