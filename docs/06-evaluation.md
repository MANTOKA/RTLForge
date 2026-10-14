# 06 · 评测协议与消融变量

## M1 首要对照：只改变编排层

旧 Orchestrator 与 LangGraph 入口必须使用同一模型版本、提示词、规格、候选策略、75 分阈值、修复预算、工具镜像、oracle 可见性和同一只读 Memory 快照。每道题生成两个独立 `task_id`，以 `experiment_pair_id` 关联。记录 `orchestrator=legacy_orchestrator` 或 `orchestrator=langgraph`，并保留任务级配置。两条入口重新评测；旧历史数字不作为新入口成绩。

生成 TB 是额外变量。**编排等价组**可在新入口先生成 TB，但先只记录，不让其结果改变候选选择、Router 或停止条件；**功能组**再启用生成 TB 的早期反馈。这样分别判断编排迁移和 Testbench Agent 的影响。生成 TB PASS 不得冒充 golden PASS。

## M1 必记字段

- 结果：`golden_pass`、`generated_tb_pass`、终止原因、失败分类。
- 预算：实际候选数、修复次数、LLM 调用数、EDA 调用数、token、耗时、成本。
- 证据：每个候选的源码哈希、静态分、Docker ToolResult、最小反例、修复谱系、实验开关与镜像版本。
- 验收：新旧入口的功能通过题目集合和逐题路径差异；任何退化先归因，不能只比最终百分比。

M1 消融：`tb_generated_before_coder`、Coder/TB 可见性、Memory 开关、`max_retry=3/4/5`、原候选策略与单候选。M2 消融：`doc_rag_enable`、`enable_waveform_summary`、`enable_yosys_synth_check` 逐项开启。评分 × Memory 的主消融继续保留，具体设计见[项目规格书](VERILOG_AGENT_PROJECT_SPEC_V2_ZH.md)。

Human v2 的 specification-only 与 oracle-assisted closed loop 是不同协议；使用 hidden golden TB 反馈循环的结果必须如实标注，不能写成普通 Pass@1。基准和 Memory 的数据隔离见[数据策略](BENCHMARK_AND_MEMORY_DATA_POLICY_ZH.md)。
