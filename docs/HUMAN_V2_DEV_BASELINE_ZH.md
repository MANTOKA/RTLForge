# Human v2 开发基线与 Debugger 首轮证据

日期：2026-09-03

## 1. 协议

- 数据：外部只读 VerilogEval-Human-v2 `dataset_dumpall`。
- 模型：`gpt-5.6-terra`，OpenAI-compatible Responses API。
- 每题候选数：2；无 Debugger 基线的最大修复次数为 0。
- 验证：golden `test.sv` 与 `ref.sv` 只挂载给 Docker verifier，未传给 Planner、Generator 或 Debugger。
- 工具：Icarus Verilog 11.0、Verilator 4.038、Yosys 0.9。
- 修正后镜像：`verilog-agent-sandbox:reflection`，镜像 ID
  `sha256:0cb21ab7aa439b645a5765aec15b7f9bbb318b5a355e78543e19f1884948a56b`。

本报告是开发集诊断，不是正式 Human v2 成绩。正式成绩仍需固定 benchmark commit、完整题集、
排除规则、模型采样参数、token/时间预算和重复运行次数。

## 2. 固定 10 例无 Debugger 基线

修正仿真裁决器后，对每题两个真实 Generator 候选重新运行隐藏测试。20 个候选均为
`Mismatches: 0`。

| 用例 | 类型 | cand_01 | cand_02 |
|---|---|---:|---:|
| `Prob009_popcount3` | 组合逻辑 | PASS | PASS |
| `Prob018_mux256to1` | 大选择器 | PASS | PASS |
| `Prob027_fadd` | 组合算术 | PASS | PASS |
| `Prob035_count1to10` | 计数时序 | PASS | PASS |
| `Prob066_edgecapture` | 状态捕获 | PASS | PASS |
| `Prob080_timer` | 定时器 | PASS | PASS |
| `Prob086_lfsr5` | LFSR | PASS | PASS |
| `Prob100_fsm3comb` | FSM | PASS | PASS |
| `Prob124_rule110` | 大状态更新 | PASS | PASS |
| `Prob137_fsm_serial` | 串行 FSM | PASS | PASS |

题级 pass@2 为 10/10；候选级通过为 20/20。由于样本量小且是开发选择集，不能外推为完整
Human v2 通过率。

## 3. 困难扩展集与真实失败

| 用例 | cand_01 | cand_02 | 结论 |
|---|---:|---:|---|
| `Prob140_fsm_hdlc` | 0 mismatch | 0 mismatch | PASS |
| `Prob144_conwaylife` | 0 mismatch | 0 mismatch | PASS |
| `Prob154_fsm_ps2data` | 978 mismatches | 978 mismatches | 自然功能失败；是否属于周期偏移待诊断 |
| `Prob155_lemmings4` | 76 mismatches | 76 mismatches | 自然功能失败；是否属于边界时序待诊断 |

两题均通过接口、Lint 和编译，失败只在隐藏功能仿真暴露，证明 Static RTL Quality Score
不能替代 Verification。

`Prob153_gshare` 另暴露出 Verilator `BLKLOOPINIT` 工具不兼容：两个候选的 Icarus 隐藏仿真均为
0/1083 mismatch。该事件不得计为 Generator 语法失败。门禁现已把这一诊断归为
`TOOL_INCOMPATIBILITY`，允许 Icarus 可编译候选继续接受强制功能验证。

本轮自然样本未出现真正的 RTL 语法错误或仿真超时。因此语法与超时路由目前由确定性回归夹具
覆盖，不能虚报为 Human v2 自然失败分布。

## 4. 仿真裁决漏洞

旧 wrapper 只检查非零退出码和 `FAIL/ERROR` 文本。Human v2 testbench 可在返回码为 0 时仅输出
`Mismatches: N`，导致 `Prob154` 和 `Prob155` 被误判为 PASS。现在 wrapper 会解析 mismatch 数字：

- `N = 0`：可继续判定 PASS；
- `N > 0`：返回 `ASSERTION_FAILURE`，Verification 归一化为 `FUNCTIONAL_MISMATCH`。

这是本轮最重要的验证基础设施修复；此前由旧镜像产生的 Human v2 PASS 均需用新镜像复核。

## 5. Router 与真实 Debugger

Adaptive Reflection Router 已实现为确定性服务：

| 失败类别 | 路由策略 | 是否调用 Debugger |
|---|---|---:|
| Syntax/Lint/Interface | `SYNTAX_REFLECTION` | 是 |
| Functional/Latency/Reset | `FUNCTIONAL_REFLECTION` | 是 |
| Timeout | `DEADLOCK_TERMINATION_REFLECTION` | 是 |
| Constraint trade-off | `REQUIREMENT_REFLECTION` | 等待用户 |
| System/Tool incompatibility/Unknown | `INFRASTRUCTURE_RECOVERY` | 否 |

真实 Debugger 只接收 SpecIR、当前 RTL、路由策略、受控诊断和剩余预算。它输出新 child candidate；
原候选保持不变，本地生成 unified diff，随后必须重新经过接口、Lint、编译、评分和隐藏仿真。

首个成功闭环采用单独标记的 defect injection，不计入自然基线：

- 原始缺陷：`Prob035_count1to10` 候选把递增写成递减；
- 初始验证：`FUNCTIONAL_MISMATCH`，386/439 mismatch；
- 路由：`FUNCTIONAL_REFLECTION`；
- Debugger 补丁：把 `q - 4'd1` 局部修复为 `q + 4'd1`；
- 修复结果：静态质量分 100，隐藏仿真 PASS；
- 证据：`runs/repair_replay_907116a416/repair_replay.json` 与其 `repair.patch`。

对 `Prob154` 的自然失败进行了两次 Debugger 请求，但中转 API 分别发生 TLS EOF 和远端无响应断开，
均未返回补丁。这是外部基础设施中断，不计为 Debugger 修复失败；未继续无界重试。

## 6. 下一步

1. 在中转 API 稳定后，重放 `Prob154`、`Prob155`，每题固定最多 1 次修复。
2. 从更多 Human v2 困难题收集自然失败，但分别报告自然失败与 defect injection。
3. 给功能诊断增加不泄露 oracle 的 mismatch 数量、首个失败周期和输出信号摘要。
4. 做 `统一 Debugger prompt` 与 `Adaptive Router` 消融，比较成功率、修复轮数、仿真次数、token 和墙钟时间。
5. 只有通过修复后隐藏验证的轨迹才进入后续 Episodic Memory。
