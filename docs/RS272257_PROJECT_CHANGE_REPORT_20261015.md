# 低功耗 RS FEC 需求提出后 VerilogAgent 项目变化与试验结果

汇报日期：2026-10-15。代码目录：`D:\AI\VerilogMulti\VerilogAgent`。本报告只归纳与 RS(272,257) BM/RiBM 试验直接有关的变更和操作，不把同期的 RTLLM、Memory、Web 等工作算作本任务成果。

## 一页结论

项目从“根据需求生成 RTL，并做静态检查/仿真”的既有流程，扩展出了一条 **RS(272,257) KES 专项实验链**：冻结算法与接口规格 → 软件参考模型/固定向量 → Planner/Generator 生成 RTL → 静态门禁 → 独立黄金 TB 仿真 → Debugger 尝试修复 → 单独生成 Agent TB → Vivado OOC 综合诊断。现已实际跑通“生成 RTL/TB、调用仿真器、调用 Vivado 并取得报告”的工具链，但 **BM、RiBM 的生成 RTL 均未通过功能验收**。这不是完整 FEC 译码器，也没有达到任务书的面积、功耗、时延目标。

核心边界：此次 RTL 顶层是 `rs_kes_bm` / `rs_kes_ribm`，只实现关键方程求解器（KES）的实验接口；完整译码器还需要 syndrome 硬件、Chien 搜索、错误值计算/纠正、帧协议和顶层控制。RS(272,257)、GF(2^10)、多项式 `0x409`、首根 α¹、10 ns 试验时钟是本次**显式实验约定**，不能直接宣称符合某项以太网标准。

## 1. 需求怎样落地

老师提出的是物理实现后的面积/功耗/时延问题；算法研究由师兄负责，本项目先验证 Agent 能否把 BM/RiBM 从规格推进到 RTL、TB、仿真和综合。因此本轮不比较两算法优劣，也不把 CFFT、RMT 或 20% 面积目标写成已实现功能。首先选 KES 中间里程碑，控制单次生成难度；完整 RS FEC 译码器作为后续阶段。

规格整理在上级目录 `D:\AI\VerilogMulti\rs_baselines\RS272257_AGENT_REQUIREMENT.md`，包含完整译码器目标、KES/系统编码约定、协议、TB 和综合要求。依据用户提供书籍第 4 章的 BM Algorithm 2，以及 RiBM Algorithm 3/图 4.4；当前 KES 代码使用前 14 个 syndrome、最多 7 个错误符号，额外第 15 个 syndrome 留给完整译码后校验。相关算法和接口解释为实验设计，尚需与老师的最终标准码型核对。

另在 `D:\AI\VerilogMulti\rs_baselines\` 保存了两套**不同参数**的开源 BM/RiBM 上游 RTL/TB 与来源说明。它们只用于参考，未改名冒充本轮 Agent 生成结果，也尚未接入 RS(272,257) 的统一验收。

## 2. 新增或调整的功能与代码位置

| 位置 | 本次作用 | 状态/边界 |
|---|---|---|
| `verilog_agent/benchmarks/rs_reference.py` | GF(2^10) 运算、系统编码、syndrome、BM/RiBM 软件 KES、译码回环及固定向量生成 | 独立测试参考，不是 RTL；尚未与外部成熟 RS 库完成整码交叉验证 |
| `verilog_agent/benchmarks/rs_demo.py` | RS 专项入口：冻结规格；生成 160 组向量、黄金 TB、hash 清单；复用现有 Planner/Generator、静态门禁、Verifier、Debugger；生成/运行 Agent TB；保存分阶段记录 | 试验脚本，不是通用 RS/FEC 产品接口；每算法 1 个初始候选、最多 1 次修复 |
| `verilog_agent/agents/testbench.py` | 新增独立的模型 TB 生成角色，只接收规格与 8 组抽样向量，不接收 DUT RTL；提示适配 Icarus，禁止不支持的 `break`/`continue` | Agent TB 是辅助测试，不代替独立黄金 TB |
| `verilog_agent/providers/openai_responses.py` | 为 Responses 请求增加可选 SSE 流式；必须收到完成事件才接受结果；增加可选 `reasoning.effort`，用于 DeepSeek 默认推理耗尽输出额度时控制请求 | 影响通用 provider；默认行为保持不指定 effort，只有显式选项才发送；流式并未解决本次中转站故障 |
| `verilog_agent/benchmarks/rs_synth.tcl` | 读取 RTL 和时钟 XDC、执行 Vivado 非工程模式 OOC 综合，输出 utilization、timing 和 checkpoint | 目前由实验操作显式调用，**尚未封装成 Agent 自动综合/报告解析服务** |
| `verilog_agent/benchmarks/vivado_smoke.v`、`vivado_parts.tcl` | 测试 Vivado 安装、许可证、器件和基础时序报告链路 | 冒烟文件，不是 BM/RiBM 设计 |
| `tests/unit/test_rs_reference.py`、`test_rs_testbench_agent.py` | 检查书中小例、有限域、编码/译码回环、TB 角色合同 | 证明测试模型的已检查行为，不证明生成 RTL 正确 |
| `tests/integration/test_rs_harness.py` | 用受控回放桩检查黄金 TB 会接受正确输出，拒绝错误系数、缺少 `done` 和提前结束 | 回放桩不是可交付 RS RTL |
| `tests/unit/test_openai_provider.py` | 补充流式完成事件和可选 DeepSeek reasoning 参数的请求测试 | 只验证客户端封装，不验证服务稳定性 |

`rs_demo.py` 新增的实验控制项包括 `--algorithm bm|ribm`、`--env-file`、`--stage rtl|all`、`--stream`、`--request-timeout`、`--max-output-tokens`、`--reasoning-effort`、`--reuse-testbench`。显式 `--env-file .env.ds` 可选用用户指定的 DeepSeek 配置而不改写原配置文件。入口拒绝覆盖既有运行目录，以保留失败证据。`RSRunner` 还要求仿真输出包含指定完成标志，避免仅凭仿真器退出码零判为通过。

这些是**专项链路和少量 provider 能力**，不是项目已经具备“自动评估物理功耗并闭环优化”的通用能力。原有多 Agent 编排、Docker 静态/仿真服务、Debugger 由本实验复用，不能重复记为本次新建。

## 3. 新增文档、目录与运行产物

| 路径 | 内容 |
|---|---|
| `D:\AI\VerilogMulti\rs_baselines\README.md`、`RS272257_AGENT_REQUIREMENT.md` | 外部 BM/RiBM 基线来源与许可边界；完整译码器试验规格草案 |
| `docs/RS272257_EXPERIMENT_ZH.md` | KES 范围、参考模型、固定向量、测试策略和最初可复现入口 |
| `docs/RS272257_LIVE_RESULT_20261014.md` | `wecodex.lol/gpt-5.6-terra` 首轮调用、流式故障及 Vivado 工具冒烟记录；其中早期“尚无 RS RTL”的文字是**当时快照**，不能当作当前结论 |
| `docs/RS272257_DEEPSEEK_TRIAL_20261014.md` | `.env.ds/deepseek-v4-pro` 的生成、TB、功能失败与 Vivado 诊断报告 |
| `docs/RS272257_PROJECT_CHANGE_REPORT_20261015.md` | 本汇报：整合需求至当前状态，区分已实现、试验成功和未达标 |
| `runs/rs_bm_*`、`runs/rs_ribm_*` | 每轮独立目录保存 `requirement.txt`、`vectors.json`、`kes_vectors.json`、`golden_tb.sv`、`manifest.json`；活跃轮次另有 Planner/Generator 产物、`agent_tb.sv`、`model_calls.jsonl`、`run_result.json` 或 `failure.json` |
| `runs/rs_provider_control_*` | 仅作中转服务对照的 RTLLM `adder_8bit` 小题；没有送 Vivado，不是 RS 成果 |
| `runs/vivado_smoke_*`、两轮 `vivado_diagnostic_cand01/` | Vivado 冒烟与 BM/RiBM 候选的原始 utilization/timing 报告和 checkpoint |

`runs/` 下目录属于本地运行证据，通常被 Git 忽略，不能仅因未显示在 `git status` 就认为已经提交或可在其他机器复现。当前工作树另有大量非 RS 改动；本报告没有把它们归入这次任务，也没有清理或覆盖。私有 `.env`/`.env.ds` 的密钥不进入汇报。

## 4. 实际做过的操作与阶段结果

1. **规格与参考模型**：从书籍明确 BM/RiBM 的 KES 版本、有限域、系数顺序和输出约定；构建 160 组覆盖 0–7 个符号错误的测试向量。BM 与 RiBM 软件模型各跑 160 次译码回环，共 320 次。固定黄金 TB 使用这 160 组；Agent TB 只接收其中 8 组抽样向量。黄金 TB 的“黄金”指独立于 DUT 的当前实验判据，不是外部认证标准。
2. **测试基础设施**：验证参考模型的书中例子、有限域计算和回环；用 Docker/Icarus 对回放桩及故障桩进行 TB 有效性测试。当前重跑 RS 相关测试结果：**27 passed**（含 2 个 Docker 集成参数用例），Ruff 检查通过，日期 2026-10-15。
3. **原中转站对照**：使用被指定的 `wecodex.lol/gpt-5.6-terra` 跑 RS 时，遇到 TB/Planner/Generator 连接或协议错误；流式请求的 BM Planner 成功，但 Generator 因 `RemoteProtocolError` 中断。再用 RTLLM 小题 `adder_8bit` 做对照：流式 Planner 收到 `response.failed`；非流式同题成功生成 RTL、过静态门禁和黄金 TB。故不能说中转站完全不可用，也不能把 RS 请求失败计作算法失败。
4. **DeepSeek 试验**：显式使用 `.env.ds` 的 `deepseek-v4-pro`。默认推理先后耗尽 8192/16384 输出 token，返回 `incomplete`；按 DeepSeek 官方 Responses API 的 `reasoning.effort=none` 重试后，两算法都生成 RTL 和 Agent TB。BM 质量分 40（门槛 75），Debugger 返回非 JSON，原始 RTL 手动黄金 TB 在 case 20 不匹配；RiBM 初始质量分 90，原始和一次修复版均在黄金 TB case 20 不匹配。两份 Agent TB 实际运行也失败。**当前没有功能验收通过的 BM/RiBM RTL。**
5. **Vivado 工具与综合**：检测到本机 Vivado 2026.1 和可用 BASIC 许可证；先用小模块验证 OOC 综合及约束读取，再对 BM/RiBM 的原始 `cand_01` 作诊断性综合。Vivado 只综合 RTL，**不综合 TB**；RTLLM 对照题没有送 Vivado。

DeepSeek 输出配额行为参考其官方 [Responses API 文档](https://api-docs.deepseek.com/api/create-response/)；模型请求、运行版本、失败文本以各轮 `model_calls.jsonl`、任务数据库和报告为准。

## 5. 当前结果与不能宣称的结果

| 项目 | BM 原始候选 | RiBM 原始候选 |
|---|---:|---:|
| RTL 文件、Agent TB 文件 | 已生成 | 已生成；另有一次修复版 RTL |
| 静态质量门槛（75） | 40，未过 | 90，通过 |
| 160 组黄金 TB | case 20 不匹配，未通过 | case 20 不匹配；修复版仍未通过 |
| Agent TB | 运行失败 | 运行失败 |
| Vivado OOC 综合 | 完成；有索引越界等警告 | 完成 |
| LUT as Logic / Slice Registers | 2562 / 453 | 2449 / 624 |
| 10 ns 约束下综合阶段 WNS | −2.675 ns，未满足 | +5.085 ns |

资源与 WNS 来自 `xc7a12ticsg325-1L` 上**功能错误候选**的 OOC 综合，仅证明工具链可工作并暴露潜在设计问题。它们不可用来给 BM/RiBM 排名，不是布局布线后的时序，更不是任务书要求的物理面积、时钟树/互连功耗或功耗改善结果。特别是 RiBM 的正 WNS 不等于设计正确；BM 虽可综合，也存在功能错误和时序违规。

本轮尚未完成：完整 RS(272,257) 译码器 RTL；BM/RiBM KES 的 160 组功能通过；自动 Vivado 调用/结构化报告提取接入 Agent 编排；布局布线和功耗分析；任务书的 CFFT/RMT 方案评估、20%+ 门数改善及实际 PPA 验收。

## 6. 建议的下一步验收顺序

1. 师兄/老师核对实验的 GF 多项式、首根、RiBM 缩放/输出约定与目标标准是否一致；如不一致，先冻结正式规格及参考向量。
2. 针对 BM/RiBM 的首个失败向量定位 GF/下标/状态更新问题，保留当前错误候选与原始报告；让 Agent 再修复时，要求结构化输出有效且静态门禁与全部 160 组黄金 TB 通过。不能只调高静态分数或修改黄金期望值。
3. 对功能验收版本重新运行 Agent TB，再调用 Vivado；将综合命令、器件、约束、utilization/timing 报告与候选 hash 绑定。若要形成 Agent 的自动闭环，还需把当前手工 Tcl 调用封装为受控工具并解析失败报告。
4. 最后扩展到完整译码器，再做布局布线、时钟树/互连和功耗分析；届时才有条件回答老师最初的“逻辑门减少但物理功耗可能上升”问题。

汇报时可用一句话概括：**我们已把 RS 的算法规格转换成可执行的 Agent/EDA 试验，并跑通生成、测试与综合链路；试验揭示当前短板主要是复杂算法 RTL 的功能正确性、Debugger 输出合同和综合后时序，尚未得到可用于 PPA 评价的正确 RS 译码器。**
