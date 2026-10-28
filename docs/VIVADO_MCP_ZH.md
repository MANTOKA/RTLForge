# Vivado MCP 综合接入

2026-10-28更新：Syndrome、BM KES及完整BM顶层均已通过本机真实MCP综合。
本版本支持菜单5多模块源码集合，以及菜单3/4携带冻结依赖。
新克隆用户须自行安装Vivado、有效许可证及官方MCP Server；本仓库不分发AMD下载物。
完整BM当前结果为160帧独立仿真PASS，OOC综合7,825 LUT、4,586 FF、10ns下WNS +2.475ns。
下文2026-10-27许可证失败记录保留为历史；不再代表当前阻塞状态。
项目整体范围见[验收矩阵](STATUS_ZH.md)。

本项目使用 AMD 官方 Vivado MCP Server，不自行实现同名替代 Server。
`VivadoMCPRunner` 是项目中的 Python MCP 客户端与受限综合适配器。
本次范围为 SystemVerilog/Verilog 单模块或冻结多模块集合的 OOC 综合、综合后时序和资源验收，
不包括布局布线、上板、功耗签核、IP 工程导入或 Ross 文档知识库/Skills。

## 输入与 Planner 的分工

用户仍然可以只输入自然语言功能需求，不必手写 SpecIR 或 TaskTree。
启用综合后，Planner 会从明确需求中提取器件、时钟周期和资源上限；未知值保留空，
不会自行选择器件或频率。运行设置中的已确认约束优先，Generator/Debugger 不能更改它们。
综合顶层与默认时钟端口取自 Planner 的 SpecIR。

`RTLTaskContext.synthesis_constraints` 与功能 SpecIR 并列，包含：

- `part`、`top`、`clock_port`、`clock_period_ns`。
- 可选的 `xdc_path`，执行前复制到证据目录并计算 SHA-256。
- 可选 `resource_limits`：`lut`、`ff`、`bram`、`dsp`；不填表示不设资源上限。
- `mode=out_of_context`、`require_timing`（默认 true）。

缺少必要约束时，流程在执行综合前暂停，控制台显示填写表单，填写后直接续到综合，
不会从需求开始重跑。API 的恢复消息为 `action=answer`，`message` 是约束字段 JSON 字符串。
项目只支持模块级时序判断，时钟约束齐全也不代表完整系统 IO 时序已签核。

## 安装与本机配置

安装依赖：`.venv/Scripts/python.exe -m pip install -e ".[graph,llm,mcp]"`。

从 AMD 官网下载独立 Windows Vivado MCP Server，并准备已获有效许可的 Vivado。
在控制台 `[2] 修改设置` 中开启综合，填写 Server 路径、Vivado 路径及约束。
设置保存在本机 `work/console_settings.json`，每次运行仍会显示配置并确认。

当前本机已配置：

- 官方 Server：项目根目录 `vivado-mcp-server-windows-amd64-2026.10.1.exe`。
- Vivado：`D:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat`。
- 器件 `xc7a12ticsg325-1L`，时钟周期 10 ns，OOC，未设置资源上限。

当前 Server 版本为 2026.10.1，SHA-256 为
`42b35150fd04939b337bba73e7bd433a08bacb71f2d6d3d72d4f472735a4011a`。
使用该二进制实际支持的 `--stdio` 原生传输；每次会话独立启动，正常结束时调用
`vivado_stop`，不依赖 Codex 自己的 MCP 配置或长期共享 HTTP daemon。

## 控制台操作

- `[1] 输入需求并运行`：启用综合时，功能验证通过后进入综合与修复闭环。
- `[3] 从已有候选续跑`：复用需求合同及 RTL，追加本轮修复预算；综合使用当前运行设置。
- `[4] 综合已有候选`：只重新进行静态检查、独立 TB 仿真和 MCP 综合，不调用 LLM；
  若失败，需要使用 `[3]` 继续修复。此入口要求提供或复用独立 TB。

现有 BM repair03 的源任务：
`runs/console/20261025_150503_724805/task_60de9a3c87c8/context.json`，选择 `cand_repair_03`。
Syndrome 的源任务：
`runs/console/20261025_010905_689008/task_2fab3ee5558e/context.json`，选择 `cand_01`。
这里的 Syndrome RTL 是该次控制台成功任务的候选，不是历史报告中另一份 RTL。

## 执行与修复

```text
静态门禁 → 功能验证 → MCP 综合 → 时序/资源验收 → 完成
                ↑                 ↓ RTL/时序/资源失败
                └─ 静态复查 ← Debugger 修复 ← 诊断/报告读取
```

修复始终生成新的候选；每份新 RTL 都重新经过静态检查、仿真及综合。
不能用旧候选的仿真 PASS 或综合报告证明新候选正确。
修复预算耗尽后失败，不会因仍有独立候选名额而跳去重新生成。

Debugger 的新增动作：

- `read_synthesis_report`：读取当前候选的结构化综合、关键路径、资源反馈。
- `run_synthesis`：仅对已完成当前候选功能检查、已配置综合的任务允许调用，
  使用同一受限执行器；计入已有 Debugger 动作预算。

模型不能直接提交任意 Tcl。执行器生成综合脚本，经官方 `vivado_start`、
`vivado_execute`、`vivado_status`、`vivado_log_messages`、`vivado_stop` 完成调用。

失败分类：

| 类别 | 行为 |
|---|---|
| `SYNTHESIS_ERROR` | Debugger 修复 RTL |
| `SYNTHESIS_TIMING_FAILED` | 保持时钟约束，修复关键路径 |
| `SYNTHESIS_RESOURCE_EXCEEDED` | 保持资源上限，修复实现 |
| `SYNTHESIS_CONSTRAINT_ERROR` | 暂停，请用户核对约束 |
| `SYNTHESIS_TOOL_ERROR` | 环境/许可证/MCP 错误，终止本轮，不消耗 RTL 修复预算 |

仅有 Agent TB 时，综合通过后仍显示“生成测试通过，未经独立验证”，保持人工复核状态。

## 证据与结果

每个候选的 `synthesis/attempt_NNN/` 独立保存：

- `synth.tcl`、`constraints.xdc`。
- `mcp_tools.json`、`mcp_calls.jsonl`、Server stderr、适配器错误日志（若有）。
- 成功执行后：`utilization.rpt`、`timing.rpt`、`critical_paths.rpt`、
  `check_timing.rpt`、`synth.dcp`、`metrics.tsv`。
- `result.json`：绑定 RTL SHA-256、XDC SHA-256、Server SHA-256/版本、器件与约束，
  分别记录综合、时序、资源与约束覆盖状态。

原任务及 RTL 不会被覆盖。只对完成报告与 checkpoint 的执行进行综合结果验收；
未运行、报告缺失、未约束路径、工具错误均不能伪装成成功。

## 2026-10-27 本机实测边界

BM repair03 与 Syndrome 均重新通过 Docker 静态检查和独立 TB；官方 MCP 初始化、
工具发现和 Vivado 启动调用已实际执行。Vivado 在综合前报错：
`Vivado Design Suite cannot be launched because a valid license was not found.`
脱离 MCP 直接启动 Vivado 也得到同样错误。

该次未能报告这两份 RTL 的新 LUT/FF/WNS，也未实测 Vivado 报告解析成功。
结构化报告解析、失败路由及修复回归使用离线测试验证；MCP 协议另有测试 Server 往返验证。
后续许可证恢复后的实测结果见 2026-10-28 记录。

证据目录：

- BM：`runs/synthesis/bm_repair03_mcp_20261027_02/`。
- Syndrome：`runs/synthesis/syndrome_mcp_20261027_01/`。

## 2026-10-28 许可证恢复后的真实综合

使用官方 Server 2026.10.1，经 `VivadoMCPRunner` 调用 Vivado 2026.1。
器件 `xc7a12ticsg325-1L`，`clk` 为 10 ns，OOC 综合，无显式资源上限。
两份源 RTL 均保持原哈希，重新通过静态门禁及独立 TB（各 160 个用例）。

| 模块 | LUT | FF | BRAM | DSP | 综合后 WNS | 综合后 WHS |
|---|---:|---:|---:|---:|---:|---:|
| Syndrome `cand_01` | 196 | 311 | 0 | 0 | +6.920 ns | +0.260 ns |
| BM `cand_repair_03` | 3545 | 611 | 0 | 0 | +2.661 ns | +0.256 ns |

两份报告均显示 `All user specified timing constraints are met.`，
`no_clock` 与 `unconstrained_internal_endpoints` 均为 0。
时序覆盖范围为时钟约束下的模块内部路径；没有设置系统级输入/输出延时，
未进行布局布线、功耗分析或上板验证，不能作为完整系统时序签核。

真实 MCP 调用记录包含 `vivado_start → vivado_execute(source synth.tcl) →
vivado_log_messages → vivado_stop`；该版本对此命令同步返回，因此没有额外轮询调用。
两份会话均确认关闭。没有调用 LLM、修改 RTL 或执行设计修复循环。

实测修正了两个适配问题，并增加回归检查：

- `check_timing` 的目录和正文含重复标题；解析器现在跳过没有正文的目录项。
  Syndrome 修正前的派生 JSON 保留为 `*.before_parser_fix.json`，原始报告未改动。
- BM 首次超过 MCP 客户端的 120 秒短请求上限；长执行命令现在使用总综合预算
  （默认 1800 秒），短查询与关闭仍使用较短上限。失败尝试保留，没有当作成功。

成功证据：

- Syndrome：`runs/synthesis/syndrome_mcp_20261028_licensed_01/summary.json`。
- BM：`runs/synthesis/bm_repair03_mcp_20261028_licensed_02/summary.json`。
- 汇总：`runs/synthesis/RS_MCP_RESULTS_20261028.md`。

官方参考：

- https://www.amd.com/en/support/downloads/ross-agentic-ai.html
- https://github.com/Xilinx/ross-ai-assistant/blob/main/docs/reference/vivado-mcp-tools.md
