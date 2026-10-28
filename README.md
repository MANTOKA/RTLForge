# RTLForge

面向 RTL 设计、验证与综合的本地智能体工程平台。**v0.3.0 · 研究预览版 · MIT**。

RTLForge 将自然语言需求转换成 SpecIR 和 TaskTree，生成 Verilog/SystemVerilog，
在 Docker 中执行接口检查、编译、lint、静态评分和仿真；失败后进行受预算约束的诊断与修复。
可选通过 AMD 官方 Vivado MCP Server 执行真实综合，并把时序与资源结果送回修复闭环。

**核心工程闭环已实现，不代表原项目书所有目标或研究假设已经完成。**
原书要求的 Compose、异步整理及评分/Memory 收益消融仍未完成。
详细边界与证据见[当前状态与验收矩阵](docs/STATUS_ZH.md)。

## 能做什么

- Planner 澄清时钟、复位、握手和时序；关键语义不清时暂停提问。
- 生成候选，经确定性工具验证后再判断结果；独立 TB 不交给生成/修复 Agent。
- Debugger 调查诊断、当前 RTL、分类 Memory 和综合报告，产生新候选后重新验证。
- 从已有候选追加修复预算续跑；输出截断时询问是否提高上限重新生成该步骤。
- 五类长期 Memory 使用分类 SQLite 文件，按角色和错误类型检索。
- 通用模块集成：选择已验证候选及合同，冻结依赖，只生成顶层；支持传递依赖。
- 可选 Vivado MCP 综合：约束、源码 hash、报告及 MCP 调用均有证据。

控制台是以上新增能力的主要入口。Web 和批量入口保留已有功能，尚未同步全部控制台能力；
本次发布不重构它们。外部文档 RAG、波形摘要、自主拆分生成全部子模块和正式消融仍待后续。

## 安装与启动（Windows PowerShell）

需要 Git、Python 3.10+（本轮使用3.12）和已启动的 Docker Desktop。

```powershell
git clone https://github.com/MANTOKA/RTLForge.git
cd RTLForge
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,graph,llm,mcp]"
docker build -t rtlforge-sandbox:latest .
Copy-Item .env.example .env
```

编辑本地 `.env`，填写自己的 `OPENAI_API_KEY`、`RTLFORGE_MODEL`；兼容服务还需正确的
`OPENAI_BASE_URL` 和 `RTLFORGE_API_STYLE=responses` 或 `chat_completions`。
调用真实模型会使用你的服务额度；本项目不提供模型密钥或 EDA 许可证。

```powershell
.\.venv\Scripts\python.exe -m rtlforge.console
```

如需双击入口，先执行 `powershell -ExecutionPolicy Bypass -File tools/build-console.ps1`，
再打开本地生成的 `RTLForge.exe`。启动器复用项目、虚拟环境和 Docker，不是独立安装包。
Linux/macOS 可使用虚拟环境中的 `python -m rtlforge.console`，不使用 Windows 启动器。

## 第一次运行

选择菜单1，依次输入以下文件的**本机绝对路径**：

1. `examples/counter/requirement.txt`
2. `examples/counter/golden_tb.sv`

确认模型、候选数、修复预算和 Memory 配置。未安装 Vivado 时保持综合关闭。
程序逐阶段显示 SpecIR、TaskTree、RTL 和检查证据的位置。
结果保存在 `runs/console/<时间>/<task_id>/`。
提供独立TB时跳过 TB Agent；未提供时只使用生成测试，并标注未经独立验证。

菜单3续跑已有候选，菜单4重新检查并综合已有候选（不调用模型），菜单5复用模块生成顶层。
[完整使用指南](docs/02-getting-started.md)包含可操作的通用集成示例。

## 验证与证据

```powershell
.\.venv\Scripts\python.exe -m rtlforge.benchmarks.single_case_demo --offline-example
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest --run-integration -m integration
```

离线示例使用脚本化模型响应，不消耗真实模型额度。最后一条需要 Docker。
MCP 协议测试使用测试 Server，不能当成真实 Vivado 综合。
真实综合需要自行安装 Vivado、配置有效许可证，并下载官方 MCP Server，见
[Vivado MCP 指南](docs/VIVADO_MCP_ZH.md)。

本地完整 BM RS(272,257) 案例通过160帧独立测试及真实 Vivado OOC 综合：
7,825 LUT、4,586 FF，10ns下 WNS +2.475ns。该案例用于证明工程链路可运行，
不证明基准普遍提升。专项 RTL、向量和任务证据保留本地，不随公开仓库分发。

## 文档与项目范围

| 内容 | 入口 |
|---|---|
| 当前状态、原项目书完成程度 | [验收矩阵](docs/STATUS_ZH.md) |
| 安装、运行、通用集成 | [使用指南](docs/02-getting-started.md) |
| 架构、Agent、Memory、评测 | [文档目录](docs/README.md) |
| 发布变化 | [CHANGELOG](CHANGELOG.md) |
| 每一步修改的时间、范围与当时结论 | [演进时间线](docs/CHANGE_HISTORY_ZH.md) |
| 贡献与测试 | [CONTRIBUTING](CONTRIBUTING.md) |
| 本地服务与执行边界 | [SECURITY](SECURITY.md) |
| 许可证及第三方材料 | [MIT](LICENSE)、[第三方声明](THIRD_PARTY_NOTICES.md) |

密钥、Memory 数据库、原始基准数据、`runs/`、AMD 可执行程序和许可证不提交。
Icarus/Verilator/Yosys 在受限 Docker 中执行；Vivado MCP 在本机执行受控脚本。
`COMPLETED` 取决于启用的检查：只有结果中实际存在综合 PASS，才可声称通过综合。
项目范围止于综合，不包含布局布线和板级验证。
