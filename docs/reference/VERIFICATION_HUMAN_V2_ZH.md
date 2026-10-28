# Verification Service 与 Human v2 基线

> 2026-10-28 发布校对：本篇保留原设计/历史实验的日期、指标与协议；其中阶段性状态不代表当前实现。当前完成范围见[验收矩阵](../STATUS_ZH.md)。

## 已实现的验证边界

Candidate Evaluation 只负责排序。Verification Service 按 `verification_order` 依次运行候选：

```text
静态排名第 1 候选 -> golden testbench
  PASS -> 完成并交付
  FAIL -> 验证排名第 2/3 候选
全部 FAIL -> 保留最高排名失败候选，输出结构化 Debugger feedback
SYSTEM_ERROR -> 停止，不调用 Debugger
```

完整 simulator 输出只进入运行证据文件；Debugger 输入不包含 golden testbench 源码和完整 transcript，
只包含失败类别、工具、最多十条结构化诊断和 timeout 标志。

## Human v2 官方参考预检

先确认 156 个 prompt/test/ref triplet 完整，再让 `RefModule` 同时作为 `TopModule` 候选运行官方差分
testbench。适配文件只写入忽略版本控制的 `runs/human-v2/reference/reference_adapters/`，不会修改原数据集。

```powershell
.\.venv\Scripts\python.exe -m rtlforge.benchmarks.verilogeval `
  --dataset-root "data\benchmarks\verilog-eval-human-v2" `
  --preflight-reference --summary-only
```

当前本地副本与工具链的结果为 `154/156 PASS`。两项异常：

- `Prob151_review2015_fsm`
- `Prob156_review2015_fancytimer`

二者均因当前 Icarus 报告 `This cast operation is not yet supported`，应记为
`TOOL_INCOMPATIBILITY`，不能记为 Generator 或 Debugger 失败。正式报告应同时保留原始 156 题结果和
排除工具不兼容后的 154 题结果。

若 Docker Hub 暂时不可访问，但本地已有包含完整 EDA 工具链的镜像，可通过
`RTLFORGE_TEST_IMAGE` 复用本地镜像运行集成测试；CI 默认仍从标准 Dockerfile 构建：

```powershell
$env:RTLFORGE_TEST_IMAGE = "rtlforge-sandbox:verification-local"
.\.venv\Scripts\python.exe -m pytest --run-integration -m integration -q
Remove-Item Env:RTLFORGE_TEST_IMAGE
```

## 单题真实闭环

下面的命令会调用真实 Planner/Generator，产生两个候选、静态排序，再运行该题 golden testbench：

```powershell
.\.venv\Scripts\python.exe -m rtlforge.benchmarks.single_case_demo `
  --human-v2-root "data\benchmarks\verilog-eval-human-v2" `
  --human-v2-case Prob001_zero `
  --static-evaluation `
  --candidates 2
```

模型调用会产生费用。开发时先选择 5-10 题，保存模型、prompt、候选数、评分配置、Docker image、
工具版本和 Git SHA。完整 156 题只在配置冻结后运行。

当前已用 `gpt-5.6-terra` 完成 `Prob001_zero` 的首个真实纵向切片：Planner 和 Generator 生成两个
候选，`cand_01` 静态分 100 并通过 golden testbench，任务最终状态为 `COMPLETED`，
`verified_rtl=true`。运行证据位于
`runs/human-v2/gpt-5.6-terra/tasks/task_563693e5c691/`；`runs/` 被 Git 忽略，不会把模型
输出或 benchmark reference 提交进产品源码。

本轮验证汇总：非 Docker 回归 `66 passed, 11 skipped`；设置本地测试镜像后，显式 Docker
集成回归 `11 passed`。
