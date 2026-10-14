# RTLLM 2.1 单题与批量评测

> 数据目录：`data/benchmarks/rtllm-2.1`
>
> 数据规模：50 个设计，分为 Arithmetic、Control、Memory、Miscellaneous 四类

## 1. 数据与可见性边界

适配器入口为 `verilog_agent/benchmarks/rtllm.py`。每题读取：

- `design_description.txt`：唯一允许 Planner、Generator 看到的输入；
- `testbench.v`：仅 Verification 使用；
- `verified_*.v`：仅用于参考预检，绝不进入 Agent 上下文或 Debugger Memory。

RTLLM 参考 RTL 的顶层模块经常带 `verified_` 前缀。适配器从描述解析正式模块名，并只在参考预检的
临时副本中重命名参考顶层。候选验证只编译隐藏 testbench 与 Agent RTL，不把参考 RTL混入候选编译。

## 2. 适配器检查

只检查 50 题目录、文件和模块名，不调用模型或 Docker：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.rtllm
```

用 Docker/Icarus 预检官方参考；可先用 `--limit 1`，确认后再去掉限制：

```powershell
$env:DOCKER_BIN = "C:\Users\16872\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe"
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.rtllm `
  --preflight-reference --limit 1 --summary-only
```

RTLLM testbench 原本面向 VCS。参考预检不通过时必须区分 RTL/测试失败、Icarus 工具不兼容和基础设施错误；
参考预检失败的题不会调用模型，也不会计为 Agent RTL 失败。

## 3. 单题可视化 Demo

```powershell
$env:DOCKER_BIN = "C:\Users\16872\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe"
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.rtllm_demo `
  --case adder_8bit `
  --static-evaluation `
  --candidates 2 `
  --max-repairs 2 `
  --memory-mode off
```

终端依次展示描述、SpecIR、Task Tree、多个 RTL 候选、静态评分、隐藏 testbench Verification，以及按需触发的
Router/Debugger。产物写入 `runs/rtllm-2.1/<model>/tasks/<task_id>/`。

只检查 API 配置而不调用模型：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.rtllm_demo `
  --case adder_8bit --check-config
```

## 4. 50 题逐题批量运行

建议先用 `--limit 1` 做烟雾测试，再开始完整运行：

```powershell
$env:DOCKER_BIN = "C:\Users\16872\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe"
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.rtllm_batch `
  --limit 1 `
  --candidates 2 `
  --max-repairs 2 `
  --memory-mode off
```

确认后删除 `--limit 1`。默认报告位置为：

- `runs/rtllm-2.1/<model>/reports/rtllm_batch.json`
- `runs/rtllm-2.1/<model>/reports/rtllm_batch.md`

批量脚本每完成一题就更新 JSON/Markdown，意外中断后可使用完全相同的参数加 `--resume`。恢复时模型、
候选数、Debugger 预算、镜像、数据集、Memory 模式或数据库发生变化会被拒绝，避免混合实验条件。

如果报告中存在 `APITimeoutError`、连接错误、限流或 HTTP 5xx 等瞬态服务错误，可在相同参数后增加：

```powershell
--resume --retry-transient-errors
```

该模式会跳过已有 PASS、稳定的 Agent 失败以及 reference/tool 异常，只移出并重跑可识别的瞬态 API/服务
错误；被移出的原结果保存在报告的 `retry_history` 中。无需再创建一个 demo。重试后的结果属于
“基础设施重试后的有效结果”，原始首轮结果仍应单独报告，不能把重试提升直接写成严格 Pass@1。

## 5. 当前验证证据

- 适配器识别 50/50 个设计；
- 官方参考 Docker/Icarus 预检：47/50 PASS，零模型调用；`ring_counter`、`asyn_fifo`、
  `freq_divbyeven` 的诊断均位于官方 testbench，作为 `TOOL_INCOMPATIBILITY` 单列；
- 单题 `adder_8bit`：任务 `task_148c99723eb8`，`cand_01` 通过静态门禁和隐藏 testbench；
- 批量烟雾测试 `accu`：任务 `task_8c8cd3bc44cc`，输出 1/1 PASS 的 JSON/Markdown 报告。

固定配置的首批 50 题评测及瞬态错误恢复已经完成，结果与口径见
`docs/RTLLM_2_1_GPT_5_6_TERRA_50_CASE_REPORT_ZH.md`。该结果不应与 Human v2 的 156 题结果直接合并。
