# 06 · 评测协议与消融变量

## M1 新旧入口对照

旧 Orchestrator 与 LangGraph 入口应使用同一模型版本、题目、候选策略、75 分阈值、修复预算、工具镜像、oracle 可见性和同一只读 Memory 快照。每道题生成两个独立 `task_id`，以 `experiment_pair_id` 关联。两条入口重新评测；旧历史数字不作为新入口成绩。由于新入口增加前置 TB 与测试目标摘要，**当前运行不是纯编排层消融**，必须同时记录多出的模型/EDA 调用并单独解释差异。

生成 TB 是额外变量。当前辅助仿真结果只记录，不改变候选选择、Router 或停止条件；未来若要证明“仅编排层”的收益，需要补一个关闭前置 TB 的图组。生成 TB PASS 不得冒充 golden PASS。

## M1 必记字段

- 结果：`golden_pass`、`generated_tb_pass`、终止原因、失败分类。
- 预算：实际候选数、修复次数、LLM 调用数、EDA 调用数、token、耗时、成本。
- 证据：每个候选的源码哈希、静态分、Docker ToolResult、最小反例、修复谱系、实验开关与镜像版本。
- 验收：新旧入口的功能通过题目集合和逐题路径差异；任何退化先归因，不能只比最终百分比。

M1 待完成的消融：前置 TB、Memory 开关、候选/修复预算，以及评分 × Memory。M2 再逐项检查文档 RAG、VCD 摘要、Yosys 门禁。当前只实现编排切换及逐任务结果记录，不把尚未完成的消融写成实验结论。设计背景见[项目规格书](reference/VERILOG_AGENT_PROJECT_SPEC_V2_ZH.md)。

Human v2 的 specification-only 与 oracle-assisted closed loop 是不同协议；使用 hidden golden TB 反馈循环的结果必须如实标注，不能写成普通 Pass@1。基准和 Memory 的数据隔离见[数据策略](reference/BENCHMARK_AND_MEMORY_DATA_POLICY_ZH.md)。
