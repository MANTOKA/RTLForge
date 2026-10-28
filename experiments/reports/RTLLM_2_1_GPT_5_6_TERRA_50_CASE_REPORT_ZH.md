# RTLLM 2.1 前 50 题评测报告：gpt-5.6-terra

> 2026-10-28 发布校对：本篇保留原设计/历史实验的日期、指标与协议；其中阶段性状态不代表当前实现。当前完成范围见[验收矩阵](../../docs/STATUS_ZH.md)。

## 1. 结论

本次请求并完成 RTLLM 2.1 全部 50 个设计的顺序评测。官方参考 RTL 在当前 Icarus 环境中有 47 题可评测，
3 题因 reference/testbench 工具不兼容单列，不计入 Agent 功能通过率。

- 原始首轮报告：40/47 PASS，成功率 85.11%；其中包含 3 个 `APITimeoutError`。
- 排除明确 API 超时的首轮已完成响应：40/44 PASS，成功率 90.91%。这是诊断口径，不是完整 47 题结果。
- 只重跑 3 个瞬态 API 超时后：42/47 PASS，成功率 89.36%。
- 对全部 50 个数据集目录计数：42/50 PASS，覆盖成功率 84.00%，但该分母包含 3 个 reference/tool 异常。
- 没有题目在 Debugger 修复后转为 PASS；`passed_after_repair = 0`。
- Memory 模式为 `off`，本次没有读取或写入 Memory，不能用于证明 Memory 提升。

原始 85.11% 必须保留；89.36% 应标注为“瞬态基础设施错误重试后的有效结果”，不能写成未经重试的严格
Pass@1。重试会重新发起模型请求，其中 Generator 阶段超时的题可能产生新的采样轨迹。

## 2. 实验配置与时间

| 项目 | 记录值 |
|---|---|
| 数据集 | RTLLM 2.1，50 个设计 |
| 模型 | `gpt-5.6-terra` |
| Provider | OpenAI-compatible Responses API |
| 协议 | `oracle_assisted_closed_loop` |
| 每题候选数 | 2 |
| Debugger 最大预算 | 2 |
| Memory | `off` |
| Docker 镜像 | `verilog-agent-sandbox:latest` |
| Icarus Verilog | 11.0 stable |
| Verilator | 4.038 |
| 原始运行开始 | 2026-09-08 22:57:14 HKT |
| 原始 50 题结束 | 2026-09-09 00:38:38 HKT |
| 原始墙钟时间 | 1 小时 41 分 24 秒 |
| 瞬态恢复开始 | 2026-09-09 00:40:03 HKT |
| 最终报告完成 | 2026-09-09 00:48:28 HKT |
| 温度 | Provider 默认值，未显式设置 |
| top-p | 未记录 |
| Token 消耗 | 当前 Provider/报告未采集 |
| API 成本 | 当前 Provider/报告未采集，无法可靠估算 |

Planner 和 Generator 只读取 `design_description.txt`；Debugger 只接收结构化诊断；golden testbench 对 Agent
隐藏，仅由 Verification 使用；官方 `verified_*.v` 只用于 reference 预检，不进入生成、调试或 Memory。
因此本结果属于隐藏测试平台参与判定的闭环评测，不等同于纯 specification-only 单次生成指标。

## 3. 原始首轮结果

| 指标 | 数量 |
|---|---:|
| 请求题数 | 50 |
| Reference 可评测 | 47 |
| PASS | 40 |
| Agent/流水线失败或错误 | 7 |
| Reference/tool 异常 | 3 |
| 报告定义成功率 | 40/47 = 85.11% |

原始 7 个未通过的可评测结果包括：

| 题目 | 原始分类 | 原因 |
|---|---|---|
| `radix2_div` | `PIPELINE_ERROR` | Planner 请求 `APITimeoutError` |
| `fixed_point_substractor` | `PIPELINE_ERROR` | Generator 请求 `APITimeoutError` |
| `edge_detect` | `PIPELINE_ERROR` | Planner 请求 `APITimeoutError` |
| `multi_pipe_4bit` | `BUDGET_EXHAUSTED` | 接口门禁修复两轮仍失败 |
| `fixed_point_adder` | `ORCHESTRATION_ERROR` | Debugger 返回未变化候选 |
| `freq_divbyodd` | `FUNCTIONAL_MISMATCH` | 三次验证均未通过，修复预算耗尽 |
| `synchronizer` | `PIPELINE_ERROR` | Planner 产生的 clock 不是合法简单 Verilog 标识符 |

## 4. 瞬态错误恢复

恢复命令保持模型、候选数、Debugger 预算、Memory 和 Docker 环境不变：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.rtllm_batch `
  --limit 50 `
  --env-file .env `
  --candidates 2 `
  --max-repairs 2 `
  --memory-mode off `
  --resume `
  --retry-transient-errors
```

脚本跳过所有已记录 PASS、稳定失败和 reference/tool 异常，只重跑 3 个瞬态 API 错误：

| 题目 | 原始结果 | 重试结果 | 任务/候选 | 修复次数 |
|---|---|---|---|---:|
| `radix2_div` | API timeout | PASS | `task_ef0367dfb756` / `cand_01` | 0 |
| `edge_detect` | API timeout | PASS | `task_29d367499856` / `cand_01` | 0 |
| `fixed_point_substractor` | API timeout | FAIL `BUDGET_EXHAUSTED` | `task_a4dbae255018` / `cand_repair_01` | 2 |

`fixed_point_substractor` 的超时问题已经排除，但真实生成/修复结果仍未通过，因此最终应计作 Agent 失败。

## 5. 最终结果与失败分析

| 指标 | 数量 |
|---|---:|
| Reference 可评测 | 47 |
| PASS | 42 |
| 最终 Agent/流水线失败 | 5 |
| Reference/tool 异常 | 3 |
| 瞬态恢复后成功率 | 42/47 = 89.36% |
| `cand_01` 成功 | 39 |
| `cand_02` 成功 | 3 |
| Debugger 修复后成功 | 0 |

按类别统计：

| 类别 | PASS | FAIL/ERROR | Reference 跳过 |
|---|---:|---:|---:|
| Arithmetic | 16 | 3 | 0 |
| Control | 5 | 0 | 1 |
| Memory | 4 | 0 | 1 |
| Miscellaneous | 17 | 2 | 1 |

最终 5 个可评测失败：

| 题目 | 最终分类 | 修复/验证情况 | 后续关注点 |
|---|---|---|---|
| `multi_pipe_4bit` | `BUDGET_EXHAUSTED` | 两轮接口修复仍被 interface gate 拒绝 | 检查参数化端口/接口解析与修复反馈 |
| `fixed_point_adder` | `ORCHESTRATION_ERROR` | 一轮后 Debugger 返回相同候选 | 增加 unchanged patch 的替代候选回退或差异约束 |
| `fixed_point_substractor` | `BUDGET_EXHAUSTED` | API 恢复后两轮接口修复仍失败 | 检查 signed、参数声明与接口比较规则 |
| `freq_divbyodd` | `FUNCTIONAL_MISMATCH` | 初始验证加两轮修复均失败 | 继续分析奇数分频相位、占空比和 testbench 时序 |
| `synchronizer` | `PIPELINE_ERROR` | Planner 阶段停止，未生成候选 | 强化 SpecIR clock 字段约束与 Planner 本地纠错 |

3 个 reference/tool 异常为 `ring_counter`、`asyn_fifo`、`freq_divbyeven`。它们的官方 reference/testbench
在 Icarus 11.0 下未通过预检，因此不归因于 Agent RTL。

## 6. 产物与限制

- 原始不可变快照：`runs/rtllm-2.1/gpt-5.6-terra/reports/rtllm_batch_initial_snapshot.json`
- 原始 Markdown 快照：`runs/rtllm-2.1/gpt-5.6-terra/reports/rtllm_batch_initial_snapshot.md`
- 恢复后 JSON：`runs/rtllm-2.1/gpt-5.6-terra/reports/rtllm_batch.json`
- 恢复后 Markdown：`runs/rtllm-2.1/gpt-5.6-terra/reports/rtllm_batch.md`
- 各题完整轨迹：`runs/rtllm-2.1/gpt-5.6-terra/tasks/<task_id>/`

`runs/` 默认不提交 Git，本报告只引用本机实验证据。当前报告没有 Token、价格、显式 temperature/top-p、API
请求 ID 等遥测，因此不能给出准确成本或完全复现实验采样；后续模型对比前应先补齐 Provider usage 遥测。
