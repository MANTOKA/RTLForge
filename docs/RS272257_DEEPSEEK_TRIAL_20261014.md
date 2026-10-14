# RS(272,257) KES：DeepSeek 试跑记录（2026-10-14）

范围：仅 BM/RiBM 的 KES，不是完整 FEC 译码器。模型来自显式 `.env.ds`：`deepseek-v4-pro`、`https://api.deepseek.com`、Responses API。API 密钥不写入报告。两种算法使用同一固定软件 oracle 和 160 组向量；黄金 TB 不进入模型提示。Vivado 2026.1 在 `xc7a12ticsg325-1L` 上做 10 ns 时钟的 OOC 综合，尚未布局布线，也未估功耗。

## 模型请求与功能验证

| 算法/运行目录 | 模型配置 | 结果 |
|---|---|---|
| BM `runs/rs_bm_ds_20261014_01` | 默认推理、8192 输出 token | Planner 以 `status=incomplete` 结束，8193 输出 token，无可解析答案 |
| BM `runs/rs_bm_ds_20261014_02` | 默认推理、16384 输出 token | Planner 成功；Generator 用满 16384 token 后 `incomplete`，无 RTL |
| RiBM `runs/rs_ribm_ds_20261014_01` | 默认推理、16384 输出 token | Planner 用满 16384 token 后 `incomplete`，无 RTL |
| BM `runs/rs_bm_ds_none_20261014_01` | `reasoning.effort=none`、16384 输出 token | Planner、Generator、Agent TB 均生成；原始 RTL 编译/接口/lint 通过但静态质量 40<75，Debugger 返回非 JSON fenced Verilog，修复流程失败。手动用固定黄金 TB 跑原始 RTL，case 20 不匹配。Agent TB 运行失败。 |
| RiBM `runs/rs_ribm_ds_none_20261014_01` | `reasoning.effort=none`、16384 输出 token | Planner、Generator、Agent TB 均生成；原始 RTL 静态质量 90≥75，但固定黄金 TB case 20 不匹配。Debugger 生成的修复版仍在 case 20 不匹配。Agent TB 运行失败。 |

DeepSeek 官方 Responses 文档说明 `max_output_tokens` 包括推理和答案，且可用 `reasoning.effort=none` 禁用默认推理。这解释了前几次输出额度耗尽的机制；`none` 让请求产生 RTL，但不保证算法正确。记录各轮原始 `model_calls.jsonl`、`run_result.json`、候选 RTL、TB 和仿真输出，不能把模型调用成功计成功能通过。

## Vivado 诊断性综合

两份**功能未通过**的原始 `cand_01` 均能完成 OOC 综合并生成 checkpoint/报告：

| 候选 | LUT as Logic | Slice Registers | 10 ns WNS | 备注 |
|---|---:|---:|---:|---|
| BM | 2562 | 453 | -2.675 ns | 时序未满足；Vivado 另有数组索引越界等警告 |
| RiBM | 2449 | 624 | +5.085 ns | 综合阶段时序满足，但功能错误 |

这组数字只用于证明综合链路与定位问题：功能错误的 RTL 不可作算法面积/时延比较；OOC 综合 WNS 不代表布局布线后的时序。时钟源 `HD.CLK_SRC` 未设置，报告也不包含最终时钟树/互连线或功耗。原始报告分别在两轮运行目录的 `vivado_diagnostic_cand01/{utilization,timing}.rpt`。

## 下一步门槛

先修复 RTL，直到**固定黄金 TB 的全部 160 组测试**通过，并使静态门禁通过；再检查 Agent TB 能否独立通过。只有这时才对验收版本重新做 Vivado 综合和（如需面积/功耗结论）布局布线/功耗分析。当前不能声称已完成 BM/RiBM 实现或完整 RS FEC。
