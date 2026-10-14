# VerilogAgent Human v2 两阶段批量测试报告

## 1. 报告范围

本报告基于以下两份机器生成的原始评测记录：

- 第一次全量评测：`runs/human-v2/gpt-5.6-terra/reports/human_v2_dev10.json`
- 第二次失败题定向复测：`runs/human-v2/gpt-5.6-terra/reports/human_v2_dev10_repairable_retry.json`
- 第二次复测 Memory 数据库：`runs/human-v2/gpt-5.6-terra/state/verilog_agent_memory.sqlite`

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

### 2.1 实验记录完整性标注

下表把后续论文复现需要的字段明确区分为“已记录”和“未记录”。`NOT_RECORDED` 表示现有产物无法
恢复准确数值，不能用猜测值补齐。

| 字段 | 第一次全量评测 | 第二次定向复测 | 后续 `Prob078` 单题 | 后续 `Prob104` 单题 |
|---|---|---|---|---|
| prompt 版本 | 未单独编号；最接近代码快照为 `bb72bbb` | 未单独编号；最接近代码快照为 `a151ef0` | Agent prompt 与 `a151ef0` 相同；使用后续 Verification 解析修正 | 同左 |
| Debugger 预算 | 1 | 3 | 3；实际 0 次 | 3；消耗 2 次调用预算，产生 1 个可验证修复候选 |
| Icarus 版本 | 11.0 stable | 11.0 stable | 11.0 stable | 11.0 stable |
| Docker 镜像 | `verilog-agent-sandbox:reflection` | 同左 | 同左 | 同左 |
| golden 可见性 | Planner/Generator 不可见；Debugger 仅结构化诊断；Verifier 黑盒可见 | 相同 | 相同 | 相同 |
| temperature | `provider_default`，未记录数值 | 同左 | `provider_default`，未记录数值 | 同左 |
| top-p | `NOT_RECORDED` | `NOT_RECORDED` | `NOT_RECORDED` | `NOT_RECORDED` |
| token 消耗 | `NOT_RECORDED` | `NOT_RECORDED` | `NOT_RECORDED` | `NOT_RECORDED` |
| API 成本 | `NOT_RECORDED`，中转 endpoint 价格未知 | 同左 | 同左 | 同左 |
| 墙钟时间 | 2:03:35.64 | 0:13:59.38 | 0:00:44.89 | 0:07:48.12 |
| 超时/系统错误 | 2 个 Icarus 工具不兼容；1 个缺少 `TopModule` 的误分类 `SYSTEM_ERROR`；无明确 API timeout | 无 timeout/system error；2 个功能失败 | 无 | 第二次 Debugger 请求发生 `APITimeoutError` |

Icarus 版本是在报告补充时从同名镜像实测得到；当前镜像 ID 为
`sha256:0cb21ab7aa439b645a5765aec15b7f9bbb318b5a355e78543e19f1884948a56b`。由于原始 JSON
只记录了可变 tag、没有记录 image ID，严格来说仍应把“历史运行使用完全相同镜像内容”视为高可信
推断，而不是已被原始报告锁定的事实。后续正式实验必须在运行开始时直接写入 Icarus 版本和 image ID。

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

### 4.1 可执行脚本位置

| 用途 | Python 脚本 | 模块入口 |
|---|---|---|
| 单题可解释 Demo | `verilog_agent/agents/demo.py` | `python -m verilog_agent.agents.demo` |
| Human v2 按题号顺序批量运行 | `verilog_agent/benchmarks/human_v2_batch.py` | `python -m verilog_agent.benchmarks.human_v2_batch` |
| 156 题读取与排序 | `verilog_agent/benchmarks/verilogeval.py` 中的 `load_human_v2()` | 按 `*_prompt.txt` 文件名排序 |
| Memory 写入/检索 | `verilog_agent/memory/store.py` | `SQLiteMemoryStore` |
| Memory 数据模型 | `verilog_agent/memory/models.py` | `RepairAttempt`、`MemoryItem` |
| Memory 与修复闭环连接 | `verilog_agent/orchestration/orchestrator.py` | `_record_repair_attempt()`、`_retrieve_memories()` |

单题运行示例：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.agents.demo `
  --human-v2-root "D:\AI\VerilogMulti\VerilogCoder-main\hardware_agent\examples\VerilogCoder\verilog-eval-v2\dataset_dumpall" `
  --human-v2-case Prob078_dualedge `
  --candidates 2 --static-evaluation --max-repairs 3 `
  --docker-image verilog-agent-sandbox:reflection
```

156 题顺序运行示例；不传 `--limit` 即运行全部 156 题：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.human_v2_batch `
  --dataset-root "D:\AI\VerilogMulti\VerilogCoder-main\hardware_agent\examples\VerilogCoder\verilog-eval-v2\dataset_dumpall" `
  --candidates 2 --max-repairs 3 `
  --docker-image verilog-agent-sandbox:reflection `
  --memory-mode read_write `
  --memory-database runs\human-v2\gpt-5.6-terra\state\verilog_agent_memory.sqlite `
  --output runs\human-v2\gpt-5.6-terra\reports\human_v2_full.json
```

批量脚本对 `load_human_v2()` 返回的排序列表逐题执行，并在每题结束后更新 JSON/Markdown，因此
中断后可使用相同配置和 `--resume` 继续。

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

### 5.3 后续单题 Demo 复核

在上述两阶段评测完成后，又分别运行了 `Prob078_dualedge` 和 `Prob104_mt2015_muxdff`：

| Problem | 测试时间（UTC+8） | Task ID | 单题结果 | 最终候选 | Debugger/停止原因 | Memory |
|---|---|---|---|---|---|---|
| `Prob078_dualedge` | 2026-09-07 18:09:29–18:10:14 | `task_ffc8d6c2030b` | PASS | `cand_02` | 0 次修复；初始 `cand_01` 为 93/224，随后 `cand_02` 为 0/224 | `off`，未写入 |
| `Prob104_mt2015_muxdff` | 2026-09-07 18:17:17–18:25:05 | `task_bf8e9f2c711b` | FAIL | `cand_02`（回退目标） | 第 1 个修复候选仍为 1/199；切换 `cand_02` 后第二次 Debugger API 请求超时 | `off`，未写入 |
| `Prob104_mt2015_muxdff` | 2026-09-08 18:15:06–18:16:31 | `task_43fb49a535b6` | PASS | `cand_repair_03` | 3 个原始候选均为 1/199；第 3 轮修复后为 0/199 | `read_only` 运行，随后离线 consolidation 写入已验证经验 |

2026-09-08 这次运行使用 `gpt-5.6-terra`、3 个候选、3 轮 Debugger 预算、Icarus Verilog 11.0 stable，
Docker 镜像 ID 为 `sha256:54f4b9f0caeb1a69a9b8d853682e2909d6f77c7cbf9e5404fc9222af952b38d2`。
该次产物仍未记录 temperature、top-p、token 消耗和 API 成本，因此这些字段继续标为 `NOT_RECORDED`。

`Prob078` 这次通过不是 Debugger 或 Memory 的作用，而是未固定 seed 的新一轮 Generator 生成了不同
的第二候选。失败的 `cand_01` 使用时钟电平直接选择两个边沿寄存器，得到 93 个 `q` mismatch；通过的
`cand_02` 则先分别生成 `clk & d_rise` 和 `~clk & d_fall` 两个中间 wire，再用第二级连续赋值 OR 输出。
对 golden testbench 的事后审计表明，参考实现特意通过 `always @(*)` 中的非阻塞赋值引入 delta-cycle
延迟；新 `cand_02` 的两级连续赋值传播恰好与该仿真调度对齐，而前一次批量运行生成的直接 ternary
或阻塞组合选择会过早改变 `q`。因此，本次 PASS 更像是“新候选匹配了 golden 仿真调度语义”，不能
视为同一 RTL 的稳定重现，也不能证明真实硬件存在对应的物理延迟。

上述 golden 细节只用于测试后的人工原因分析，没有进入 Planner、Generator、Debugger 或 Memory。
若把 `Prob078` 与 2026-09-08 的 `Prob104` 最新单题 PASS 都作为补充证据回填，则当前暂定有效结果为
154/154，即 **100%**；按全部 156 题计算为 154/156，即 98.72%。这不是新的全量运行结果，必须与
第二次批量回填结果 152/154 分开报告，也不能当作稳定复现实验。

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

2026-09-08 的离线 consolidation 保留上述历史记录，并从 172 个任务摘要中形成 153 条
`DESIGN_PATTERN`、13 类 `ERROR_PATTERN` 和 8 条 `REPAIR_STRATEGY`（4 条 ACTIVE）。新增 Prob104
策略只保存可迁移的首样本初始化修复和验证证据，不保存参考 RTL、golden testbench 或完整答案。

`Prob131_mt2015_q4` 和 `Prob147_circuit10` 没有 Debugger 修复轨迹，所以没有生成 repair memory；这是
当前写入门限的预期行为。5 条成功经验都保留在数据库中，但前 4 条在后续题目中累计达到两次负迁移，
已被安全策略标记为 `QUARANTINED`，不会继续检索；只有 `Prob154_fsm_ps2data` 的经验仍为 `ACTIVE`。

后续单题 `Prob078` 同样没有写入 Memory，原因有两个：运行参数是 `memory_mode=off`，并且它由原始
候选 `cand_02` 直接通过，没有产生 Debugger repair trajectory。当前写入机制位于
`Orchestrator._record_repair_attempt()`：每个 Debugger 子候选完成 Verification 后构造并保存
`RepairAttempt`；只有 `memory_mode=read_write`、`passed=true`、最终 mismatch 为 0（或该验证类型没有
mismatch 计数）且 rationale/expected impact 非空时，`SQLiteMemoryStore.record_repair_attempt()` 才会
调用 `_consolidate()` 和 `_upsert_memory()` 形成可检索 `MemoryItem`。直接生成即通过的 RTL 目前只保留
任务产物和验证证据，不进入 repair memory。

这一结果证明了 Memory 的“写入、检索、使用反馈、负迁移隔离”链路已经实际运行，但不能据此声称
Memory 提升了总体准确率。事实上，成功项中只有 `Prob053` 和 `Prob063` 的修复使用了检索经验；
`Prob034`、`Prob062`、`Prob154` 的最终成功修复没有使用 Memory。当前 `generic_rtl +
FUNCTIONAL_MISMATCH` 的检索粒度偏粗，导致不相关经验被跨题注入并很快隔离，是下一阶段需要解决的
问题。

## 7. 剩余问题与结论边界

1. `Prob078_dualedge` 的原始 simulator 输出存在“单输出 mismatch 非零、聚合 mismatch 为 0”的
   不一致。第二次报告仍按旧解析器记录为 0/224；后续单题运行使用修正后的解析逻辑，从输出级计数
   正确恢复出 `cand_01` 的 93/224，并验证 `cand_02` 为 0/224。当前实现保留原始 transcript，且在
   aggregate 为 0、输出级计数非零时以输出级求和作为 `total_mismatched_samples`，避免该类真实失败
   被误判为 PASS。
2. 第二次批量复测中的 `Prob078_dualedge` 没有用满 3 轮：第三次 Debugger 返回了与父候选完全相同的
   RTL，被完整性检查拒绝。后续单题虽已 PASS，但属于重新生成候选后的新证据，不能修改原批次事实。
3. 第二次批量复测中的 `Prob104_mt2015_muxdff` 用满 3 轮且始终为 1/199；2026-09-08 的后续单题在
   新增首样本时序分类和跨题 Memory 检索后，经 3 轮修复达到 0/199。原批次 FAIL 保持不变。
4. 两道 Icarus cast 不兼容题仍应单列工具链异常。可使用支持相应 SystemVerilog cast 的仿真器复核，
   但不应通过修改 Agent RTL 来“适配”官方 testbench。
5. 第二次同时改变了修复预算、候选回退、接口处理和 Memory，并重新调用了随机模型，因此
   `+4.55` 个百分点是“修改后系统的定向复测结果”，不能归因于某一个创新点。

结论：在当前 Human v2 与 Docker 工具链下，第一次全量评测取得 94.16% 可评测题准确率；对 9 个
可修复失败题应用新闭环后有 7 题通过，使合并后的有效结果达到 98.70%。候选回退在 `Prob034` 和
`Prob062` 中留下了直接运行证据，Memory 也完成了真实写入与负迁移隔离，但 Memory 的独立增益尚未
通过受控 A/B 实验验证。

后续单题证据进一步使 `Prob078_dualedge` 通过，但原因是重新生成的候选恰好符合 golden 的 delta-cycle
调度语义，而非 Debugger 或 Memory 修复。因而最新补充口径可写为 153/154（99.35%），正式论文指标
仍应以一次固定 prompt、固定采样参数、固定镜像 digest、完整顺序运行的 156 题实验为准。
