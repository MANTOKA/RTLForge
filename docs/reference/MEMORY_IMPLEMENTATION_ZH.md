# Human v2 证据驱动的 Memory 实现

> 2026-10-28 发布校对：本篇保留原设计/历史实验的日期、指标与协议；其中阶段性状态不代表当前实现。当前完成范围见[验收矩阵](../STATUS_ZH.md)。

> 更新日期：2026-09-08
>
> 数据来源：现有 Human v2 全量、定向复测和后续单题任务产物

## 1. 当前实现边界

项目书定义五类长期 Memory。现有 Human v2 运行证据能够安全支持其中三类：

| 类型 | 写入门控 | 使用位置 |
|---|---|---|
| `ERROR_PATTERN` | 确定性 Verification 确认失败；只保存结构化类别、输出、时序模式和证据 hash | Router / Debugger 诊断 |
| `REPAIR_STRATEGY` | 必须是 Debugger child candidate，且修复后功能验证通过 | Debugger |
| `DESIGN_PATTERN` | 候选必须通过功能验证；只保存 Generator 的短 pattern 描述，不保存 RTL | Generator |
| `PROJECT` | 从已验证的本地 RS 手工干预证据中筛选短规则，保留来源；不存 RTL/TB | 离线导入，在线可检索；Agent 不自动写入 |
| `HUMAN_INTERVENTION` | Human v2 仍无可认证的人类亲自编写 diff；RS 项目另有助手手工修改的来源/差异 hash/复验证据 | 仅 RS 专属库离线归档，`EVIDENCE_ONLY`，不在线检索 |

因此，在线 Agent 仍不是“五类 Memory 全部完成”，而是把现有数据能够证明的三类自动写入与一类受控离线规则接入，并阻止 benchmark
答案、`ref.sv`、golden testbench、完整 RTL 和未经验证的失败修复进入长期 Memory。

## 2. 在线写入和检索

- Verification 失败时写入 `ERROR_PATTERN`，失败不能冒充成功策略。
- Debugger 修复通过时写入 `REPAIR_STRATEGY`；未通过的尝试只保留为 `RepairAttempt`。
- 原始 Generator 候选功能验证通过时写入 `DESIGN_PATTERN`。
- `DESIGN_PATTERN` 只接受未经 Debugger 改写的原始 Generator 候选，并再次要求结构化 mismatch 为 0；
  旧镜像产生的“status=PASS 但 mismatch>0”产物不会进入长期 Memory。
- Planner 完成后按 module family 给 Generator 注入最多 3 条 verified design pattern。
- Router/Debugger 阶段优先检索 verified repair strategy，再补充 error pattern。
- 同一 benchmark problem 的 Memory 不回流到自身，避免答案泄漏。
- 负迁移累计达到门限的条目进入 `QUARANTINED`；同一来源已有更细 module family 的策略时，旧
  `generic_rtl` 副本进入 `SUPERSEDED`。
- `support_count` 只按不同 benchmark problem 增长；重复运行同一道题不会虚增置信度。

## 3. 离线 Consolidation

离线入口为 `rtlforge/memory/consolidate.py`。它扫描 `showcase_summary.json`，按最新成功任务为每道题
选择一个 verified design pattern，并从失败和 repair trajectory 中提取可审计条目：

```powershell
.\.venv\Scripts\python.exe -m rtlforge.memory.consolidate `
  --tasks-root runs\human-v2\gpt-5.6-terra\tasks `
  --database runs\human-v2\gpt-5.6-terra\state\rtlforge_memory.sqlite `
  --toolchain-version rtlforge-sandbox:reflection
```

2026-09-08 对现有产物的执行结果：

| 指标 | 数量 |
|---|---:|
| 扫描任务摘要 | 170 |
| 去重后的 `DESIGN_PATTERN` | 153 |
| 去重后的 `ERROR_PATTERN` | 13 |
| 数据库中的 `REPAIR_STRATEGY` | 8 |
| 当前 ACTIVE repair strategy | 4 |
| 本轮标记为 SUPERSEDED 的旧粗粒度策略 | 2 |

数据库原文件已备份为
`runs/human-v2/gpt-5.6-terra/state/rtlforge_memory_pre_consolidation_20260908.sqlite`。

该入口是可重复运行、确定性的离线 worker。目前尚未实现任务完成后由独立进程自动排队执行的异步调度；
异步队列属于下一步工程化，不影响当前前台 Agent 闭环。

## 4. Prob104 的处理

`Prob104_mt2015_muxdff` 的三个候选均只有 `1/199` mismatch，第一次 mismatch 在 `5 ps`。稳定状态下的
mux 和触发器逻辑没有持续错误；问题是无 reset 的 `Q` 在第一个时钟采样前为 unknown，而验证参考具有
确定初值。修复重点应是初始化/首周期 NBA 调度，不应继续改写 mux 选择逻辑。

本轮完成：

- Verification 为“总 mismatch=1 且总样本>1”增加 `isolated_single_sample` 时序特征和诊断重点；
- Router 将该特征细分为 `INITIAL_STATE_MISMATCH`，仍走受控 Functional Reflection；
- Debugger prompt 明确优先检查 initialization、reset assumption 和 first-cycle/NBA timing；
- module family 新增 `flip_flop`，使 Prob104 可跨题检索 Prob034/Prob053 已验证的首样本初始化修复；
- 增加 `tests/fixtures/human_v2_prob104/` 回归夹具，修复 RTL 仅增加 `initial Q = 1'b0`，不改变稳态逻辑。

当前快速测试已经验证诊断、路由、检索和 Memory 门控。通过 Docker Desktop CLI 的绝对路径重建最新镜像
后，新增 Prob104 Icarus 回归通过；真实单题任务 `task_43fb49a535b6` 使用 `memory_mode=read_only`，官方参考
先通过 golden testbench，三个原始候选均为 `1/199` mismatch。Debugger 检索到 Prob034/Prob053 的跨题
首样本经验，前两轮分别遭遇接口门禁和仍为 `1/199`，第三轮为内部寄存器增加确定初值后达到 `0/199`，
最终通过 golden testbench。随后离线 consolidation 才将这条已验证修复写入长期 Memory；同题检索隔离仍然
有效。该案例证明调用链能够利用 Memory 并收敛，不等同于证明没有 Memory 时一定失败。

本次工具链为 `rtlforge-sandbox:latest`，镜像 ID
`sha256:54f4b9f0caeb1a69a9b8d853682e2909d6f77c7cbf9e5404fc9222af952b38d2`，Icarus Verilog 11.0 stable。

## 5. RS 手工干预证据归档（2026-10-17）

`python -m rtlforge.memory.import_rs_interventions` 对固定的 RS(272,257) 运行产物核对最终 RTL SHA-256 和独立黄金仿真 PASS 后，在
`runs/rs-272-257/state/rtlforge_memory.sqlite` 归档 6 条 `HUMAN_INTERVENTION`：BM/RiBM KES、BM/RiBM Chien/Forney、BM/RiBM 顶层。每条只保存泛化的原因/适用条件/修复动作、前后源码和验证日志 hash、差异 hash、来源路径及验证命令；不存完整 RTL、黄金 TB 或参考答案。syndrome 的 Agent 首版已通过，不记为人工修复。重复导入使用固定 ID，不增加支持次数。

这里的“人工”指 Agent 流程以外的手工修改操作；实际操作者是 Codex 助手，记录明确标为 `operator=codex_assistant_manual`、`human_authored=false`。因此这些原始条目均为 `EVIDENCE_ONLY`，不是数据策略中“人类亲自编写”的可复用正向训练样本。RS 实验此前 `memory_mode=off`，当时的 Agent 没有学到这些修复。Git HEAD 只标识导入时工作区基础版本，RS 运行 RTL 在忽略目录中，具体版本由文件 SHA-256 绑定。许可证未核定，不能把本地参考实现当作可任意复用的上游 RTL。

## 6. 持久 Memory 默认启用（2026-10-17）

用户入口（单题、Human v2、RTLLM、Web）默认 `read_write`，共享 `memory_data/rtlforge_memory.sqlite`；`--memory-mode off` 只用于明确的无 Memory 对照实验，`--memory-database` 可显式覆盖。无功能验证的生成/静态任务只读取已有 Memory，不把未验证候选写成成功经验。`memory_data/` 是本机持久数据，已加入 Git 忽略规则；运行旧报告仍保持其原始 Memory 配置，不追溯改写。

一次性、可重复的导入命令：

```powershell
.\.venv\Scripts\python.exe -m rtlforge.memory.bootstrap
```

导入筛选原 Human v2 库中 170 条 `ACTIVE` 且具有适当验证门禁的条目，跳过 `SUPERSEDED` / `QUARANTINED`；RS 库的 6 条助手手工干预仍以 `EVIDENCE_ONLY` 保存，另派生 6 条保留来源的 `PROJECT` 短规则供相同模块族检索。首次导入后目标库共 182 条；后续在线任务可能继续写入。`PROJECT` 规则只有原因、适用条件和建议动作，不含 RTL、黄金 TB 或答案；它们不是 Agent 成功记录，且必须在新任务中重新验证。模块族优先取 Planner 产出的 RS 模块名，避免整套译码规格中同时出现 syndrome/KES/Chien 时误分类。

检索命中只证明提示被提供，不能证明 Memory 导致通过；应比较固定模型、seed、prompt、预算和初始 Memory 快照的 on/off 对照，并记录失败及负迁移。

## 7. 仍未完成

- 完整 `PROJECT` 的模块图、接口、参数、依赖和仓库约定 schema；目前仅有受控短规则；
- `HUMAN_INTERVENTION` 的真正人类作者/审批流程、在线检索准入，以及跨任务效果验证；RS 归档仅是隔离的证据层；
- 独立进程/队列形式的异步 Consolidation Worker；
- 语义聚类与同义 pattern 合并；
- 固定模型、seed、prompt 和预算下的 Memory off/on 对照实验。

这些项目完成前，只能声称“三类在线证据驱动 Memory + 一类受控规则检索 + 一类隔离证据归档”，不能声称五类完整在线 Memory 或 Memory 增益已经完成。
