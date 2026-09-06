# VerilogAgent Human v2 两阶段批量测试报告

## 1. 报告范围

本报告基于以下两份机器生成的原始评测记录：

- 第一次全量评测：`runs/benchmarks/human_v2_dev10.json`
- 第二次失败题定向复测：`runs/benchmarks/human_v2_dev10_repairable_retry.json`
- 第二次复测 Memory 数据库：`work/verilog_agent_memory.sqlite`

证据文件 SHA-256：

- 第一次 JSON：`1685042F67FBF8A72D2CA2BB29BDA91FE42DAA1AFE40CD9C5FECE99C1B6C8A80`
- 第二次 JSON：`254C51AE2591BB91856D3210E18C401EA1B32FE8AEF40C7AFC778ED0F27E8DED`
- Memory 数据库：`C32F4EEF5E3C98113D5A756794AEAF74185AF55D32CC08D07EB488D8501BCA72`

需要特别说明：第一次运行覆盖 Human v2 全部 156 题；第二次运行并非重新运行全部 156 题，而是从
第一次结果中筛选 8 个 `FUNCTIONAL_MISMATCH` 和 1 个可修复的旧 `SYSTEM_ERROR`，共 9 题复测。
因此，本报告同时给出“9 题复测通过率”和“将复测结果回填后的 156 题有效总体通过率”，不把第二次
定向复测表述为一次独立的 156 题全量实验。

## 2. 测试环境与时间

| 项目 | 第一次全量评测 | 第二次定向复测 |
|---|---|---|
| 数据集 | VerilogEval-Human-v2，156 题 | 第一次评测中的 9 个可修复失败题 |
| 开始时间（香港时间，UTC+8） | 2026-09-06 23:44:07 | 2026-09-07 02:16:23 |
| 结束时间（香港时间，UTC+8） | 2026-09-07 01:47:43 | 2026-09-07 02:30:22 |
| 持续时间 | 2 小时 3 分 35.64 秒 | 13 分 59.38 秒 |
| 模型 | `gpt-5.6-terra` | `gpt-5.6-terra` |
| API endpoint | `https://wecodex.lol/v1` | `https://wecodex.lol/v1` |
| 每题候选数 | 2 | 2 |
| 最大修复轮数 | 1 | 3 |
| Docker 镜像 | `verilog-agent-sandbox:reflection` | `verilog-agent-sandbox:reflection` |
| Memory | 未启用 | `read_write` |
| Agent 可见信息 | Planner/Generator 仅 prompt；Debugger 仅结构化诊断 | 同左 |
| Golden oracle | Verification 黑盒使用 testbench 和 reference | 同左 |
| temperature / seed | provider 默认值 / 未配置 | provider 默认值 / 未配置 |

API Key 没有写入报告。两次运行的模型、endpoint、候选数和 Docker 镜像一致，但最大修复轮数与
Memory 配置不同，而且模型采样没有固定 seed，因此这不是严格的单变量消融实验。

## 3. 第一次全量评测结果

### 3.1 总体指标

| 指标 | 结果 |
|---|---:|
| 数据集总题数 | 156 |
| 可评测题数 | 154 |
| PASS | 145 |
| FAIL | 9 |
| 官方参考/工具不兼容而跳过 | 2 |
| 可评测题准确率 | **145 / 154 = 94.16%** |
| 按全部 156 题计算的覆盖通过率 | 145 / 156 = 92.95% |
| 经 1 轮 Debugger 修复后通过 | 7 |

第一次运行中，经 Debugger 修复后通过的题为：`Prob070_ece241_2013_q2`、
`Prob102_circuit3`、`Prob141_count_clock`、`Prob149_ece241_2013_q4`、
`Prob150_review2015_fsmonehot`、`Prob152_lemmings3` 和 `Prob155_lemmings4`。
第一次运行尚未启用当前 Memory MVP，因此这些成功修复没有写入本次 Memory 数据库。

### 3.2 第一次未通过内容

| Problem | 第一次结果 | 修复轮数 | 失败证据/原因 |
|---|---|---:|---|
| `Prob034_dff8` | FAIL | 1 | `FUNCTIONAL_MISMATCH`，1/41 samples |
| `Prob053_m2014_q4d` | FAIL | 0 | `FUNCTIONAL_MISMATCH`，1/100 samples |
| `Prob062_bugs_mux2` | FAIL | 1 | `FUNCTIONAL_MISMATCH`，85/114 samples |
| `Prob063_review2015_shiftcount` | FAIL | 1 | `FUNCTIONAL_MISMATCH`，1880/2071 samples |
| `Prob078_dualedge` | FAIL | 1 | 报告为 `FUNCTIONAL_MISMATCH`，聚合计数异常显示 0/224 |
| `Prob104_mt2015_muxdff` | FAIL | 0 | `FUNCTIONAL_MISMATCH`，1/199 samples |
| `Prob131_mt2015_q4` | FAIL | 0 | 旧分类为 `SYSTEM_ERROR`；实际诊断是缺少 testbench 要求的 `TopModule` |
| `Prob147_circuit10` | FAIL | 0 | `FUNCTIONAL_MISMATCH`，4/232 samples |
| `Prob154_fsm_ps2data` | FAIL | 1 | `FUNCTIONAL_MISMATCH`，978/1619 samples |
| `Prob151_review2015_fsm` | SKIPPED | 0 | Icarus：`This cast operation is not yet supported` |
| `Prob156_review2015_fancytimer` | SKIPPED | 0 | Icarus：`This cast operation is not yet supported` |

两道 `TOOL_INCOMPATIBILITY` 属于官方参考/testbench 与当前 Icarus 工具链的兼容性问题，没有交给
Debugger 修改 RTL，也没有纳入第二次 9 题复测。

## 4. 两次运行之间的修改

两次运行之间主要完成了以下修改：

1. 将批量测试默认最大修复轮数从 1 提高到 2，本次复测显式使用 3 轮。
2. 保存每一轮 Verification 历史，使 Debugger 能看到修复前后 mismatch、上一轮 rationale、
   expected impact 和是否取得进展。
3. 当修复后 mismatch 不下降时，不再盲目沿同一分支重复修改，而是回退到尚未修复的原始候选；
   初始候选排名与当前修复候选的验证顺序分开保存，避免 `cand_02` 丢失。
4. Debugger 被明确要求：上一轮无进展时必须更换根因假设，不能重复等价修改。
5. Human v2 Planner 强制遵守 `TopModule` 接口；缺少该模块不再误判为基础设施故障，而是可修复的
   `INTERFACE_MISMATCH`。
6. 批量输出增加 `task_id`、最终候选、修复轮数、失败原因、停止原因和候选回退信息。
7. 增加 `off`、`read_only`、`read_write` 三种 Memory 模式；本次复测使用 `read_write`。
8. Memory 保存全部修复轨迹，但只有经过 Verification 功能通过的修复才形成可检索策略；同一道
   benchmark problem 禁止检索自身经验，也不保存完整 prompt、RTL、testbench 或参考 RTL。

对应 Git 提交为：

- `271599f feat: improve repair fallback and add memory MVP`
- `a151ef0 fix: rerun repairable Human v2 interface failures`

## 5. 第二次定向复测结果

### 5.1 总体指标

| 指标 | 结果 |
|---|---:|
| 定向复测题数 | 9 |
| PASS | 7 |
| FAIL | 2 |
| 复测子集通过率 | **7 / 9 = 77.78%** |
| 复测中经 Debugger 修复后通过 | 5 |
| 重新生成候选后直接通过 | 2 |
| Debugger 修复尝试总数 | 13 |

将第二次结果替换第一次对应失败项后：

| 合并后的 156 题指标 | 第一次 | 修改并复测后 | 变化 |
|---|---:|---:|---:|
| 可评测 PASS | 145/154 | 152/154 | +7 题 |
| 可评测题准确率 | 94.16% | **98.70%** | **+4.55 个百分点** |
| 按全部 156 题计算 | 92.95% | 97.44% | +4.49 个百分点 |
| 剩余 RTL 失败 | 9 | 2 | -7 题 |
| 工具不兼容 | 2 | 2 | 不变 |

### 5.2 九题逐题结果

| Problem | 第二次结果 | 最终候选 | 修复轨迹 | 关键结果 |
|---|---|---|---|---|
| `Prob034_dff8` | PASS | `cand_repair_02` | 2 轮；第 1 轮 1→1，回退 `cand_02`；第 2 轮 1→0 | 候选回退生效 |
| `Prob053_m2014_q4d` | PASS | `cand_repair_01` | 1 轮，1→0 | 使用 1 条检索 Memory |
| `Prob062_bugs_mux2` | PASS | `cand_repair_03` | 3 轮，111→111、111→111、111→0 | 回退 `cand_02` 后改变根因假设 |
| `Prob063_review2015_shiftcount` | PASS | `cand_repair_01` | 1 轮，1886→0 | 使用 1 条检索 Memory |
| `Prob078_dualedge` | FAIL | `cand_repair_02` | 2 轮，报告均为 0→0；第 1 轮后回退 `cand_02` | 第 3 次调用返回未改变 RTL，触发 `ORCHESTRATION_ERROR` |
| `Prob104_mt2015_muxdff` | FAIL | 报告回滚到 `cand_repair_02` | 3 轮，1→1、1→1、1→1 | 预算耗尽，三种假设均无进展 |
| `Prob131_mt2015_q4` | PASS | `cand_01` | 0 轮 | `TopModule` 接口约束修复后直接通过 |
| `Prob147_circuit10` | PASS | `cand_01` | 0 轮 | 新一轮 Generator 候选直接通过 |
| `Prob154_fsm_ps2data` | PASS | `cand_repair_01` | 1 轮，1008→0 | 修复滑动窗口/三字节 FSM 语义 |

`Prob147_circuit10` 的改善发生在重新生成候选后，没有触发 Debugger；由于 seed 未固定，不能把它
单独归因于修复逻辑。`Prob131_mt2015_q4` 则与强制 `TopModule` 的确定性接口修正直接一致。

## 6. Memory 写入结果

第二次复测共记录 13 条 `RepairAttempt`，其中 5 条最终通过 Verification，并各自形成 1 条
`REPAIR_STRATEGY`，因此 Memory 数据库中共有 5 条已验证成功经验。

| 来源 Problem | Memory ID | 成功修复摘要 | 当前状态 | 正/负迁移计数 |
|---|---|---|---|---:|
| `Prob034_dff8` | `mem_c30d1aff8f112e37c3b1` | 为无 reset 的触发器输出补充确定初值，1→0 | QUARANTINED | 1 / 2 |
| `Prob053_m2014_q4d` | `mem_e3c07d4f51ddff46fefa` | 避免无初值状态参与首次 XOR，1→0 | QUARANTINED | 0 / 2 |
| `Prob062_bugs_mux2` | `mem_d5867d41b88feb3f4320` | 两种 mux 写法无效后反转选择极性，111→0 | QUARANTINED | 1 / 2 |
| `Prob063_review2015_shiftcount` | `mem_3c2ab74a5b9022355d66` | 修正串行输入的移位方向，1886→0 | QUARANTINED | 0 / 2 |
| `Prob154_fsm_ps2data` | `mem_16600073aeb3ffa53532` | 将错误滑动窗口改为三状态消息收集 FSM，1008→0 | ACTIVE | 0 / 0 |

`Prob131_mt2015_q4` 和 `Prob147_circuit10` 没有 Debugger 修复轨迹，所以没有生成 repair memory；这是
当前写入门限的预期行为。5 条成功经验都保留在数据库中，但前 4 条在后续题目中累计达到两次负迁移，
已被安全策略标记为 `QUARANTINED`，不会继续检索；只有 `Prob154_fsm_ps2data` 的经验仍为 `ACTIVE`。

这一结果证明了 Memory 的“写入、检索、使用反馈、负迁移隔离”链路已经实际运行，但不能据此声称
Memory 提升了总体准确率。事实上，成功项中只有 `Prob053` 和 `Prob063` 的修复使用了检索经验；
`Prob034`、`Prob062`、`Prob154` 的最终成功修复没有使用 Memory。当前 `generic_rtl +
FUNCTIONAL_MISMATCH` 的检索粒度偏粗，导致不相关经验被跨题注入并很快隔离，是下一阶段需要解决的
问题。

## 7. 剩余问题与结论边界

1. `Prob078_dualedge` 的原始 simulator 输出存在“单输出 mismatch 非零、聚合 mismatch 为 0”的
   不一致。第二次报告仍按旧解析器记录为 0/224，导致 Debugger 收到的进展信号不准确；当前工作区
   已出现针对该解析问题的未提交修改，但它不属于本次第二次运行的已验证结果。
2. `Prob078_dualedge` 没有用满 3 轮：第三次 Debugger 返回了与父候选完全相同的 RTL，被完整性检查
   拒绝，任务以 `ORCHESTRATION_ERROR` 停止。批量摘要优先展示最后一次 Verification 类别，因此表面
   仍显示 `FUNCTIONAL_MISMATCH`，后续应同时保留“功能根因”和“实际停止类别”。
3. `Prob104_mt2015_muxdff` 已用满 3 轮且 mismatch 始终为 1/199，需要更细的首个 mismatch 时间、
   输出级证据或时序语义分类，继续增加相同诊断下的修复次数意义有限。
4. 两道 Icarus cast 不兼容题仍应单列工具链异常。可使用支持相应 SystemVerilog cast 的仿真器复核，
   但不应通过修改 Agent RTL 来“适配”官方 testbench。
5. 第二次同时改变了修复预算、候选回退、接口处理和 Memory，并重新调用了随机模型，因此
   `+4.55` 个百分点是“修改后系统的定向复测结果”，不能归因于某一个创新点。

结论：在当前 Human v2 与 Docker 工具链下，第一次全量评测取得 94.16% 可评测题准确率；对 9 个
可修复失败题应用新闭环后有 7 题通过，使合并后的有效结果达到 98.70%。候选回退在 `Prob034` 和
`Prob062` 中留下了直接运行证据，Memory 也完成了真实写入与负迁移隔离，但 Memory 的独立增益尚未
通过受控 A/B 实验验证。
