# Development baseline

> 2026-10-28 发布校对：本篇保留原设计/历史实验的日期、指标与协议；其中阶段性状态不代表当前实现。当前完成范围见[验收矩阵](../STATUS_ZH.md)。

This file defines the minimum repository conventions. It is intentionally small and may
evolve through reviewed configuration-schema versions as the implementation grows.

## Configuration

Committed defaults live in `config/default.toml`. Runtime configuration follows this
precedence, from lowest to highest:

1. `config/default.toml`;
2. an optional task or deployment configuration file;
3. environment-variable overrides;
4. explicit API or CLI arguments.

The default file contains no credentials. Secrets belong in environment variables or an
ignored local `.env` file and must never be written into run metadata, logs, test fixtures,
issues, or pull requests. Any incompatible configuration change increments
`schema_version` and documents its migration.

## Logging

Application and orchestration logs use one JSON object per line on `stderr`. Each event
should include `timestamp`, `level`, `event`, `task_id`, and `component` when those values
exist. Tool results remain machine-readable JSON on `stdout`; diagnostic logs must not
corrupt that output. Keys listed in `logging.redact_keys` are redacted before emission.

Generated task artifacts and per-task logs belong under `runs/<task_id>/`, which is ignored
by Git. Tests should assert structured fields rather than compare unstable prose logs.

## Repository artifacts

- `tests/fixtures/` contains hand-written, stable inputs used as regression rulers.
- `runs/<task_id>/` contains generated RTL and task artifacts and is never committed.
- `config/default.toml` contains safe, versioned defaults only.
- `.env` and local overrides contain developer-specific values and are never committed.

Every implementation change starts from an Issue, uses a short-lived branch, includes the
relevant tests and documentation, and enters `main` through a pull request with passing CI.

## Model-provider environment

The real Planner and Generator read `OPENAI_API_KEY` and `RTLFORGE_MODEL` at runtime.
`OPENAI_BASE_URL` may point to another endpoint that implements the OpenAI Responses API and
strict JSON Schema output. Timeouts, output limits, and SDK retries can be overridden with
`RTLFORGE_REQUEST_TIMEOUT_SECONDS`, `RTLFORGE_MAX_OUTPUT_TOKENS`, and
`RTLFORGE_MAX_RETRIES`. The API key is excluded from object representations and model
responses are requested with `store=false`; it must still never be placed in a committed file.
