# 02 · 快速开始

以下命令从仓库根目录执行。需要 Python 3.10+；Docker EDA 任务还需要可用的 Docker Desktop。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
docker build -t verilog-agent-sandbox .
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.single_case_demo --offline-example
.\.venv\Scripts\python.exe -m pytest -q
```

离线示例和单元测试不消耗模型额度。真实 Docker 回归另用：

```powershell
.\.venv\Scripts\python.exe -m pytest --run-integration -m integration
```

真实模型路径需要本地配置 `OPENAI_API_KEY`、`VERILOG_AGENT_MODEL`，必要时配置兼容的 `OPENAI_BASE_URL`。密钥只放在环境变量或忽略提交的本地 `.env`。先运行 `single_case_demo --check-config` 核对非敏感配置；具体单题、批量命令见 [Human v2 指南](HUMAN_V2_SINGLE_AND_BATCH_ZH.md)。基准数据放在忽略提交的 `data/benchmarks/`，布局见[数据说明](../data/benchmarks/README.md)。

当前入口是旧 Orchestrator；M1 LangGraph 入口建成前，上述命令不代表 LangGraph 流程。Web 工作台见[现有说明](WEB_WORKBENCH_ZH.md)。
