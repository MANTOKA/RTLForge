# VerilogAgent 文档总目录

本目录参考 [Adaptive-rag 的文档组织](https://github.com/JEen-wang/Adaptive-rag/tree/main/docs)：根 README 负责入口，主题页各讲一件事，实验报告独立留存。本文档只描述仓库中已存在的能力或明确标为计划的机制。

| 问题 | 主题页 | 深入材料 |
|---|---|---|
| 项目解决什么问题？ | [01 项目概览](01-overview.md) | [当前进展快照](CURRENT_PROGRESS_ZH.md) |
| 如何运行？ | [02 快速开始](02-getting-started.md) | [开发约定](DEVELOPMENT.md) |
| 新旧编排怎样共存？ | [03 架构](03-architecture.md) | [当前状态机](PHASE2_ORCHESTRATION.md) |
| Agent 可以调用什么？ | [04 Agent 与工具](04-agents-and-tools.md) | [Agent 实现](REAL_AGENTS.md)、[工具接口](TOOL_INTERFACE.md) |
| Memory 与文档检索有何区别？ | [05 Memory 与 RAG](05-memory-and-rag.md) | [Memory 实现](MEMORY_IMPLEMENTATION_ZH.md) |
| 如何公平评测？ | [06 评测](06-evaluation.md) | [Human v2 运行](HUMAN_V2_SINGLE_AND_BATCH_ZH.md) |
| M1/M2/M3 的边界是什么？ | [07 路线图](07-roadmap.md) | [原完整规格书](VERILOG_AGENT_PROJECT_SPEC_V2_ZH.md) |

## 专项资料与历史报告

- 设计与溯源：[完整项目计划书](VERILOG_AGENT_PROJECT_SPEC_V2_ZH.md)、[静态候选评估](STATIC_CANDIDATE_EVALUATION_ZH.md)、[基准与 Memory 数据策略](BENCHMARK_AND_MEMORY_DATA_POLICY_ZH.md)。
- 使用与界面：[Planner/Generator 演示](PLANNER_GENERATOR_SHOWCASE_ZH.md)、[Web 工作台](WEB_WORKBENCH_ZH.md)。
- Human v2：[开发基线](HUMAN_V2_DEV_BASELINE_ZH.md)、[单题与批量](HUMAN_V2_SINGLE_AND_BATCH_ZH.md)、[双轮报告](HUMAN_V2_TWO_RUN_EVALUATION_REPORT_ZH.md)、[验证说明](VERIFICATION_HUMAN_V2_ZH.md)。
- 其他实验：[RTLLM](RTLLM_EVALUATION_ZH.md)、[RS 实验](RS272257_EXPERIMENT_ZH.md)、[RS 改动记录](RS272257_PROJECT_CHANGE_REPORT_20261015.md)、[token 统计](TOKEN_ACCOUNTING_ZH.md)。

旧报告保留原路径和当时的实验口径；主题手册反映当前代码与已确认的下一阶段设计。历史成绩不自动代表 LangGraph 新流程结果。
