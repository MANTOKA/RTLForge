# 01 · 项目概览

VerilogAgent 面向自然语言到 RTL 的生成与修复。目标是让每条候选 RTL 都有明确规格、工具证据、失败原因和可回溯修复记录。

## 当前可用

- `RealPlanner` 生成 `SpecIR` 与 Task Tree；`RealGenerator` 生成候选；`RealDebugger` 根据最小验证反馈修复。
- `StaticCandidateEvaluator` 做接口、Docker 编译、Lint 和质量评估；`GoldenTestbenchVerificationService` 做功能验证；Router 与预算控制修复循环。
- 一次性 Docker 沙盒预装 Icarus、Verilator、Yosys。`SQLiteMemoryStore` 提供设计、错误、修复三类验证证据检索；SQLite 另保存事件与任务检查点。
- M1 LangGraph 入口现已调用通用 Testbench Agent，在 Coder 前生成自检 TB；生成 TB 不是最终 oracle。

## 已确认的改造

M1 增加 LangGraph 入口和前置 Testbench Agent，复用现有 Agent、EDA、评分、Router、Memory。旧 Orchestrator 作为对照入口保留；LangGraph 的进程内 checkpoint 与永久审计 SQLite 分开。M2 再加入外部协议文档 RAG、VCD 摘要和 Yosys 综合门禁。M3 处理多模块 Master/Assembler。新入口尚无重新完成的 Human v2 成绩，不宣称优于旧入口。

有独立 golden testbench 的任务，`golden_pass` 表示该基准验证通过。生成的 TB 通过只表明通过有限自动测试。没有独立 oracle 的任务返回 `generated_tb_pass_await_manual` 等状态。静态分和通用综合统计不能替代功能验证或真实芯片 PPA。

代码入口：[领域模型](../verilog_agent/domain/models.py)、[旧编排](../verilog_agent/orchestration/orchestrator.py)、[LangGraph 编排](../verilog_agent/orchestration/graph.py)、[Docker 工具网关](../verilog_agent/services/candidate_evaluation.py)。
