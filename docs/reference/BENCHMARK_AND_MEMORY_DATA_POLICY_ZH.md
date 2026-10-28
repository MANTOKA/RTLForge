# Benchmark 与 Memory 数据策略

> 2026-10-28 发布校对：本篇保留原设计/历史实验的日期、指标与协议；其中阶段性状态不代表当前实现。当前完成范围见[验收矩阵](../STATUS_ZH.md)。

## 现在就开始测试，不等 Debugger

测试分三层引入：

1. `smoke`：10-20 个快速、人工审阅的小任务，每次提交运行，保护接口、评分和状态机。
2. `regression`：来自真实历史缺陷的错误类别与修复案例，在 Verification/Debugger 开始后持续增长。
3. `benchmark`：冻结的 VerilogEval-Human v2、RTLLM 等，只用于阶段评测和发布，不作为反复调 prompt 的题库。

本阶段先接入 Human v2 的目录检查和可见性隔离。Planner/Generator 只能读取 `*_prompt.txt`；
`*_test.sv` 只交给 Verification；`*_ref.sv` 只用于基准预检，永不进入 Agent prompt 或 Memory。

```powershell
.\.venv\Scripts\python.exe -m rtlforge.benchmarks.verilogeval `
  --dataset-root "D:\AI\VerilogMulti\VerilogCoder-main\hardware_agent\examples\VerilogCoder\verilog-eval-v2\dataset_dumpall"
```

通过率运行要等下一步 Verification Service 能统一执行 golden testbench 并记录失败类别后开始。首次只跑
5-10 题 smoke，确认协议和费用，再运行冻结的 156 题；不能未经用户确认直接消耗 156 题的模型调用。

## 非 AI 编造的数据来源分工

| 来源 | 推荐用途 | 是否进入长期 Memory | 约束 |
|---|---|---:|---|
| HWE-bench | Error / Repair Strategy，真实 fail-to-pass 修复 | 是，优先 | 只取 Verilog/SystemVerilog 子集；保留上游 commit、测试证据和许可证 |
| RTLLM 2.0 | 规格到 RTL 的外部评测、已验证设计模式候选 | 有条件 | benchmark 题与 Memory 构建集严格隔离，防止答案泄漏 |
| VerilogEval-Human v2 | Planner/Generator 基线与闭环通过率 | 否 | 156 题冻结为 benchmark；答案和 testbench 不进入 Memory |
| CORE | 设计模式与项目模块候选 | 暂缓 | CC BY-NC 4.0，且在采用前还要逐项核实原创来源和可商用边界 |
| 真实开源项目 PR/commit | Project / Error / Repair Strategy | 是 | 必须能复现 fail-to-pass；保存项目、commit、diff、测试和许可证 |

不作为长期 Memory 正向素材：LLM 自己生成的“错误题”、随机 mutation、没有测试证据的代码片段、
VerilogEval 的参考答案、golden testbench 内容、来源不明的网上答案。它们最多可用于隔离的鲁棒性测试，
不能被标记为已验证经验。

## 防污染与质量门控

- Memory 条目必须记录 `source_repository`、`source_commit`、`license`、`human_authored`、
  `verification_command`、`failure_signature`、`patch_hash` 和验证结果。
- 只有真实工具链复验成功的人工修复或真实开源提交才能进入长期正向 Memory。
- 正式评测按题号/电路族隔离；当前题及其同构变体不能出现在可检索 Memory 中。
- 评分权重用开发集校准，在冻结验证集确认，最终 benchmark 只运行并报告，不能再据结果改权重。
