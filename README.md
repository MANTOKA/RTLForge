# VerilogAgent：面向 RTL 生成与验证的多 Agent 系统

VerilogAgent 将硬件需求整理为规格，生成 Verilog/SystemVerilog RTL，并在受限 Docker 容器中调用 Icarus Verilog、Verilator 和 Yosys 检查候选实现。验证失败后，系统按错误类型触发受预算约束的修复。项目保留静态评分和验证证据驱动的 Memory，目标是提高 RTL 生成的正确率与可追溯性。

> 当前状态：旧 Orchestrator 的 Planner → Generator → 静态评估 → golden-testbench Verification → Router → Debugger 闭环可运行。独立 Testbench Agent 目前用于 RS 实验；LangGraph 主入口、通用工具调用、LangSmith 追踪和外部文档 RAG 尚未接入主流程。本页按实际代码与后续里程碑区分状态。

## 快速导航

| 想了解 | 文档 |
|---|---|
| 目标与当前边界 | [01 项目概览](docs/01-overview.md) |
| 安装、离线演示与测试 | [02 快速开始](docs/02-getting-started.md) |
| 旧入口与目标 LangGraph 架构 | [03 架构](docs/03-architecture.md) |
| Agent、工具权限和 Docker EDA | [04 Agent 与工具](docs/04-agents-and-tools.md) |
| 历史 Memory 与外部文档 RAG | [05 Memory 与 RAG](docs/05-memory-and-rag.md) |
| Human v2、对照与消融口径 | [06 评测](docs/06-evaluation.md) |
| M1/M2/M3 实施顺序 | [07 路线图](docs/07-roadmap.md) |
| 全部主题和历史报告 | [文档总目录](docs/README.md) |

## 当前执行路径

```mermaid
flowchart LR
    U[需求] --> P[Planner]
    P --> G[Generator]
    G --> E[接口检查、编译、Lint、静态评分]
    E --> V[隐藏 golden testbench 验证]
    V -->|通过| O[RTL 与证据]
    V -->|失败| R[Router]
    R --> D[Debugger]
    D --> E
```

当前默认先生成一个独立候选；硬门禁失败或静态质量分低于 75 时按预算生成下一个候选。静态合格后进入功能验证，功能失败优先修复；无进展或修复预算耗尽时再尝试新候选。默认最多两个独立候选和三次修复。所有 EDA 命令由确定性服务通过一次性 Docker 容器执行，模型不直接获得通用 shell 权限。

M1 将新增独立 LangGraph 入口，复用现有 Agent、评分、EDA 和 Memory 服务；旧入口保留用于相同配置下的对照。Testbench Agent 将在 Coder 前生成辅助自检 TB，但生成 TB 的 PASS 不等于隐藏 golden TB 的功能 PASS。详见[架构](docs/03-architecture.md)与[评测协议](docs/06-evaluation.md)。

## 仓库地图

```text
verilog_agent/
  agents/           Planner、Generator、Debugger；实验性 Testbench Agent
  orchestration/    当前 Orchestrator、状态机、事件和 SQLite 审计
  services/         候选评估、Verifier、Router、Docker EDA 网关
  memory/           已验证设计、错误和修复策略的 SQLite 检索
  providers/        模型提供方适配
  benchmarks/       Human v2、RTLLM、RS 实验入口
  web/              本地 Web 工作台代码
tool_wrapper.py      容器内 EDA 操作与统一 ToolResult
Dockerfile           非 root EDA 沙盒镜像
config/              可提交的默认配置
tests/               单元、集成与固定回归夹具
docs/                按主题组织的手册与实验记录
data/benchmarks/     本地基准数据；不提交仓库
runs/                生成的任务证据；不提交仓库
```

计划中的 LangGraph 图模块和文档检索模块只会在对应里程碑实现时加入仓库。

## 本地快速开始

在 Windows PowerShell 中，从仓库根目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
docker build -t verilog-agent-sandbox .
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.single_case_demo --offline-example
.\.venv\Scripts\python.exe -m pytest -q
```

离线演示和单元测试不调用真实模型。Docker 集成测试另用 `pytest --run-integration -m integration`；它要求 Docker Desktop 可用。真实模型需要在本地环境配置密钥与兼容的 Responses API；运行前可用 `--check-config` 查看不含密钥的有效配置。详细步骤见[快速开始](docs/02-getting-started.md)。

## 评测与安全边界

- Human v2 的 `ref.sv` 和 golden testbench 只供 Verifier 内部使用，不进入 Planner、Generator、Debugger、可检索 Memory 或 LangSmith 原始追踪。
- 生成 TB 用于开发反馈；有 golden TB 时，最终功能通过以独立 golden TB 为准。没有独立 oracle 的真实用户任务应单独标明验证置信度。
- Icarus、Verilator、Yosys 只在 Docker 沙盒内运行；RTL/TB 与审计证据可持久化在宿主机，作为只读输入挂入容器。
- 静态评分、综合统计不等于功能正确，也不等于目标芯片的真实 PPA。
- 历史报告使用其各自记录的模型、配置和协议。M1 改造完成后重新运行新旧入口对照，不把历史百分比当成新流程的成绩。

仓库采用 Git 管理代码与文档；密钥、基准原始数据和 `runs/` 产物不得提交。项目仍在研究与工程迭代阶段，具体限制见[项目概览](docs/01-overview.md)。
