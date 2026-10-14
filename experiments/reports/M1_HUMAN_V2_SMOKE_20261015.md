# M1 Human v2 单题真实模型烟测（2026-10-15）

目的仅为确认新旧入口在真实模型、Docker EDA 与隐藏 golden 验证下可运行；不是通过率比较或消融实验。

共同配置：Human v2 `Prob001_zero`；模型 `gpt-5.6-terra`；独立候选上限 1；修复上限 1；Memory `off`；实验配对 ID `m1-smoke-20261015`。两个任务使用不同 `task_id`，未固定模型输出，因此不能把差异直接归因于编排方式。

| 入口 | golden 结果 | 生成 TB | 模型调用 | 修复次数 |
|---|---|---|---:|---:|
| LangGraph | PASS | PASS | 3 | 0 |
| 旧 Orchestrator | PASS | 不适用 | 2 | 0 |

本地原始 JSON 和 Markdown 证据分别保存在忽略提交的 `runs/m1_human_v2_graph_smoke.*` 与 `runs/m1_human_v2_legacy_smoke.*`。新入口多出的 1 次模型调用是 Testbench Agent；两组都没有覆盖失败后 Debugger 修复分支。下一步需在固定模型与题目集合上完整重跑，并分别报告功能通过、调用/Token/耗时和逐题差异。
