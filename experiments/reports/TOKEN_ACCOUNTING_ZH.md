# LLM 调用与 token 计数

> 2026-10-28 发布校对：本篇保留原设计/历史实验的日期、指标与协议；其中阶段性状态不代表当前实现。当前完成范围见[验收矩阵](../../docs/STATUS_ZH.md)。

Planner、Generator、Debugger 在 Orchestrator 中自动接入 Provider 调用记录器，
不改变结构化 JSON 返回值、提示词或候选/修复策略，包括重生成和每轮修复调用。

## 记录位置

- 每题任务目录的 `model_calls.jsonl`：每次调用结束即追加，失败也记录。
- SQLite checkpoint 的 `metadata.model_calls`：正常恢复 checkpoint 时继续累计。
- `showcase_summary.json` 的 `model_calls`、`token_usage`：逐次明细和按角色汇总。
- Human v2、RTLLM 批量 JSON 的逐题结果与 `metrics.token_usage`；Markdown 显示已知总量和覆盖率。
- Planner/Generator 阶段失败的批量错误结果也保留 `task_id` 和调用用量。
- RTLLM 重试汇总包括 `retry_history.replaced_results` 中已经消耗的调用。

每条含本地唯一 `call_id`、`task_id`、`role`、`schema_name`、请求 `model`、
服务端 `response_model`（若返回）、输入/输出/缓存 token、`latency_ms`、
`request_id`、`response_id`、`temperature`、`seed`、调用状态和 usage 状态。
服务端请求 ID 与响应 ID 分开存储。不保存 API key、提示词、RTL 或原始响应。

## API 字段映射

| 统一字段 | Responses usage | Chat Completions usage |
|---|---|---|
| `input_tokens` | `input_tokens` | `prompt_tokens` |
| `output_tokens` | `output_tokens` | `completion_tokens` |
| `cached_input_tokens` | `input_tokens_details.cached_tokens` | `prompt_tokens_details.cached_tokens` |

Responses 缓存字段参考 [OpenAI 官方文档](https://developers.openai.com/api/docs/guides/prompt-caching)。
缓存 token 已包含在输入 token 中，不要重复相加。

## 统计口径

- 输入输出均返回：`REPORTED`；仅返回一个：`PARTIAL`；均未返回：`NOT_REPORTED`。
- 不支持 usage 的 Provider（包括 ScriptedProvider）仍记录调用，token 为 `null`。
- 缓存字段缺失也为 `null`，只有服务端返回 0 才记 0。
- 汇总仅加已知值，全部未知时保留 `null`；每个 token 字段提供 `*_reported_calls`，
  同时提供 `reported_calls`、`partial_calls`、`not_reported_calls`、`failed_calls`。
- `by_role` 区分三个 Agent；`untracked_tasks` 表示旧数据缺少调用明细，旧运行无法补算真实 usage。
- JSON 解析失败仍保留 usage；连接失败/额度失败通常为 `NOT_REPORTED`。
- `temperature`、`top_p`、`seed` 为配置值；`sampling.request_parameters` 是请求参数，
  `response_sampling` 单独保存服务端回显（缺失为 `null`），不会覆盖配置值。
- 统计一次 `generate_structured` 调用；SDK 内部重试的耗时包含在内，但 token 仅取最终可观察响应。
  此计数不保证与供应商账单完全相同。
- 目前只计量，不自动估价或设置强制限额；后续成本控制应同时检查用量覆盖率。

新 Provider 可调用 `capture_response` 接入返回的 usage；普通入口自动挂接包装器。
直接调用单独的 Agent/Provider 时，可显式使用 `RecordingProvider(provider, role="generator")`
和 `model_call_task(context)`。未绑定任务时 `task_id=null`，记录可从包装器的 `calls` 获取。

## 固定基准采样配置

默认值（可在 `.env` 或环境变量中覆盖，三个 Agent 共用）：

```dotenv
VERILOG_AGENT_TEMPERATURE=0.1
VERILOG_AGENT_TOP_P=1.0
VERILOG_AGENT_SEED=20261008
```

每次请求显式传入 temperature、top_p；当前 Qwen + 阿里云 DashScope Chat 路径还传入 seed。
其他 Responses 路径不发送 seed，报告仍显示配置的数值，但标记 `NOT_SENT_UNSUPPORTED`，
不能据此声称 seed 已生效。`SENT` 只表示请求参数包含 seed，不代表服务端保证确定性。
不兼容模型若拒绝参数，会正常报错，不会偷偷去掉参数后重试。

Human v2 / RTLLM 的批量 JSON、Markdown、单题摘要和控制台配置检查均显示明确数值。
逐次调用另存服务端模型 `response_model`、`system_fingerprint`（若返回）与采样回显。
请求模型名可能是浮动别名，服务端未提供具体版本时，不宣称已锁定模型快照。

报告 `prompt_version` 是三个 Agent 源码与 Provider 格式化代码的 SHA-256 指纹；
逐次记录另有指令和 schema 的 SHA-256，不保存原始提示词。
候选策略和修复上限沿用报告现有字段。恢复批量运行时，采样配置或提示词源码指纹
不一致将拒绝继续，避免将不同实验混在一起；旧报告缺少这些信息也需要开启新运行，
不能用当前默认值补写历史参数。历史报告与已完成的测试结果不作修改。

固定采样配置只降低波动，不保证云端结果完全可复现。接口依据：
[OpenAI Responses](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)、
[DashScope Chat Completions](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions)。

Provider 接口由 `VERILOG_AGENT_API_STYLE` 明确选择：`responses` 或
`chat_completions`。保留 `auto` 仅用于兼容旧 DashScope 配置；调用记录中的
`sampling.api_style` 保存实际解析后的协议。
