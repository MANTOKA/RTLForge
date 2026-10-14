# Docker 候选静态评审

## 目的与边界

这个阶段把生成的每个 RTL 候选交给确定性的 Docker EDA 沙盒，而不是让 LLM 自己宣称“通过”。
每个候选依次执行：

1. `interface`：对照 `SpecIR` 检查模块名、端口名称、方向和位宽；
2. `lint`：Verilator 静态检查，错误是硬门槛，警告进入扣分项；
3. `compile`：Icarus Verilog 编译；
4. `eval_quality`：按版本化配置生成静态质量分和逐条扣分证据。

只有硬门槛全部通过且静态分达到阈值的候选才有资格被选择。先按质量分降序排序，分数相同时
按候选编号升序选择，所以结果可重复。初始权重位于
`config/static_score_v1.json`，包括配置版本、阈值和每种 Verilator 诊断的扣分；这些权重是待用
benchmark 校准的初始假设，不是已经证明的研究结论。

这不是功能验证：没有自动 testbench 时，系统不会运行 `simulate`，也不会声称 RTL 功能正确。
演示会停在 `VERIFYING`，并显示 `verified_rtl: false`。下一阶段才是自动 testbench、仿真、
失败分类和真实 Debugger 修复。

## 运行方式

先确认 Docker Desktop 正在运行，并且已经有本地沙盒镜像：

```powershell
docker image inspect verilog-agent-sandbox:latest
```

无需模型调用的两候选演示：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.agents.demo `
  --offline-example --static-evaluation
```

真实 Planner/Generator，加三候选评审：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.agents.demo `
  --static-evaluation --candidates 3
```

默认候选数是 2，以控制模型费用和 Docker 时间。真实 Generator 支持 `--candidates 2` 或
`--candidates 3`；离线固定示例只有两个脚本化候选，因此不支持 `--offline-example --candidates 3`。

## 结果与证据

每个任务会在 `runs/<task_id>/` 中保存：

```text
tool_runs/
├── cand_01_interface.json
├── cand_01_compile.json
├── cand_01_lint.json
├── cand_01_eval_quality.json
└── ...
static_evaluation.json
showcase_summary.json
```

`static_evaluation.json` 中每个候选包含项目书规定的 `ScoreCard`：

```json
{
  "candidate_id": "cand_02",
  "static_quality_score": 88,
  "threshold": 75,
  "is_acceptable": true,
  "deductions": [],
  "gate_stage": "STATIC_SCORED",
  "config_version": "score-v1"
}
```

终端会显示每个候选的通过状态、质量分、工具、耗时和错误类别，并显示
`selected_candidate` 与 `selected_artifact`。容器没有网络、根文件系统只读、以非 root 用户运行；
候选文件也必须位于该任务自己的 `runs/` 根目录内。

## Fake Debugger 的位置

Fake Debugger 只用于状态机测试。它不会写出真正 RTL 修复文件，因此不能用于真实候选的选择或
交付。候选选择由上面的确定性评审服务完成；真实 Debugger 会在后续仿真失败且诊断已结构化后
接入。
