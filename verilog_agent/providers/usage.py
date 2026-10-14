from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from verilog_agent.providers.base import StructuredModelProvider

_task: ContextVar[Any] = ContextVar("model_call_task", default=None)
_response: ContextVar[dict | None] = ContextVar("model_call_response", default=None)
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens")


def prompt_source_version() -> str:
    """Fingerprint prompt builders, schemas and provider formatting, not task data."""
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for relative in ("agents/planner.py", "agents/generator.py", "agents/debugger.py",
                     "providers/openai_responses.py"):
        digest.update(relative.encode("utf-8"))
        digest.update((root / relative).read_text(encoding="utf-8").encode("utf-8"))
    return "sha256:" + digest.hexdigest()


def _get(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def capture_response(response: Any, *, chat: bool = False) -> None:
    """Capture only accounting fields; never keep prompts, RTL, or credentials."""
    record = _response.get()
    if record is None:
        return
    usage = _get(response, "usage")
    input_key, output_key = (
        ("prompt_tokens", "completion_tokens") if chat else ("input_tokens", "output_tokens")
    )
    record.update(
        input_tokens=_count(_get(usage, input_key)),
        output_tokens=_count(_get(usage, output_key)),
        cached_input_tokens=_count(_get(_get(usage, input_key + "_details"), "cached_tokens")),
        request_id=_get(response, "_request_id") or _get(response, "request_id"),
        response_id=_get(response, "id"),
        response_model=_get(response, "model"),
        response_sampling={key: _get(response, key) for key in ("temperature", "top_p", "seed")},
        system_fingerprint=_get(response, "system_fingerprint"),
        api="chat_completions" if chat else "responses",
    )


@contextmanager
def model_call_task(context):
    token = _task.set(context)
    try:
        yield
    finally:
        _task.reset(token)


class RecordingProvider:
    """Observe the existing structured contract without changing Agent logic."""

    def __init__(self, provider: StructuredModelProvider, *, role: str) -> None:
        self.provider = provider
        self.role = role
        self.calls: list[dict] = []

    def generate_structured(self, **kwargs: Any) -> dict[str, Any]:
        context = _task.get()
        settings = getattr(self.provider, "settings", None)
        sampling = (settings.sampling_configuration()
                    if hasattr(settings, "sampling_configuration") else {})
        record = {
            "call_id": uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "task_id": context.task_id if context is not None else None,
            "role": self.role,
            "schema_name": kwargs["schema_name"],
            "model": _get(settings, "model"),
            "input_tokens": None, "output_tokens": None, "cached_input_tokens": None,
            "request_id": None, "response_id": None,
            "temperature": sampling.get("temperature"), "top_p": sampling.get("top_p"),
            "seed": sampling.get("seed"),
            "sampling": sampling,
            "response_sampling": None,
            "prompt_sha256": hashlib.sha256(kwargs["instructions"].encode("utf-8")).hexdigest(),
            "schema_sha256": hashlib.sha256(json.dumps(
                kwargs["schema"], sort_keys=True, ensure_ascii=False,
            ).encode("utf-8")).hexdigest(),
            "status": "SUCCESS",
        }
        token = _response.set(record)
        started = perf_counter()
        try:
            return self.provider.generate_structured(**kwargs)
        except Exception as error:
            record["status"] = "ERROR"
            record["error_type"] = type(error).__name__
            cause = error.__cause__ or error
            record["request_id"] = record["request_id"] or getattr(cause, "request_id", None)
            raise
        finally:
            _response.reset(token)
            record["latency_ms"] = round((perf_counter() - started) * 1000)
            present = sum(record[key] is not None for key in TOKEN_FIELDS[:2])
            record["usage_status"] = ("NOT_REPORTED", "PARTIAL", "REPORTED")[present]
            self.calls.append(record)
            if context is not None:
                context.metadata.setdefault("model_calls", []).append(record)
                path = context.metadata.get("model_calls_path")
                if path:
                    destination = Path(path)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with destination.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def summarize_calls(calls: list[dict]) -> dict:
    """Known totals plus explicit coverage; unknown usage is never inferred as zero."""
    def totals(rows):
        result = {
            "call_count": len(rows),
            "reported_calls": sum(r.get("usage_status") == "REPORTED" for r in rows),
            "not_reported_calls": sum(r.get("usage_status") == "NOT_REPORTED" for r in rows),
            "partial_calls": sum(r.get("usage_status") == "PARTIAL" for r in rows),
            "failed_calls": sum(r.get("status") == "ERROR" for r in rows),
            "latency_ms": sum(r.get("latency_ms", 0) for r in rows),
        }
        for key in TOKEN_FIELDS:
            known = [r[key] for r in rows if r.get(key) is not None]
            result[key] = sum(known) if known else None
            result[key + "_reported_calls"] = len(known)
        return result

    return {
        **totals(calls),
        "by_role": {role: totals([r for r in calls if r["role"] == role])
                    for role in sorted({r["role"] for r in calls})},
    }


def batch_usage(results: list[dict]) -> dict:
    result = summarize_calls([call for row in results for call in row.get("model_calls", [])])
    result["untracked_tasks"] = sum(
        "model_calls" not in row and row.get("status") != "SKIPPED_REFERENCE" for row in results
    )
    return result


def usage_markdown(usage: dict) -> str:
    def display(key):
        return usage.get(key) if usage.get(key) is not None else "NOT_REPORTED"
    return (
        f"- LLM calls: {usage.get('call_count', 0)}; known input/output/cached tokens: "
        f"{display('input_tokens')}/{display('output_tokens')}/{display('cached_input_tokens')}; "
        f"usage-reported calls: {usage.get('reported_calls', 0)}; "
        f"untracked tasks: {usage.get('untracked_tasks', 0)}"
    )


def sampling_markdown(sampling: dict) -> str:
    return (
        f"- Sampling (configured): temperature={sampling.get('temperature', 'UNKNOWN')}; "
        f"top_p={sampling.get('top_p', 'UNKNOWN')}; seed={sampling.get('seed', 'UNKNOWN')}; "
        f"seed status={sampling.get('seed_status', 'UNKNOWN')}. "
        "Server echoes are recorded separately in model_calls.response_sampling; "
        "fixed parameters do not guarantee identical outputs."
    )
