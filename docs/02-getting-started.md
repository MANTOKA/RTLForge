# 02 · 快速开始

以下命令从仓库根目录执行。需要 Python 3.10+；Docker EDA 任务还需要可用的 Docker Desktop。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,graph]"
docker build -t verilog-agent-sandbox .
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.single_case_demo --offline-example
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.single_case_demo --offline-example --orchestrator langgraph --static-evaluation
.\.venv\Scripts\python.exe -m pytest -q
```

离线示例和单元测试不消耗模型额度。真实 Docker 回归另用：

```powershell
.\.venv\Scripts\python.exe -m pytest --run-integration -m integration
```

真实模型路径需要本地配置 `OPENAI_API_KEY`、`VERILOG_AGENT_MODEL`，必要时配置兼容的 `OPENAI_BASE_URL`。密钥只放在环境变量或忽略提交的本地 `.env`。先运行 `single_case_demo --check-config` 核对非敏感配置；具体单题、批量命令见 [Human v2 指南](reference/HUMAN_V2_SINGLE_AND_BATCH_ZH.md)。基准数据放在忽略提交的 `data/benchmarks/`，布局见[数据说明](../data/benchmarks/README.md)。

`--orchestrator legacy` 是旧入口（默认）；`--orchestrator langgraph --static-evaluation` 是新入口。新任务默认 `strict_sv`；Human v2 题目为 `legacy_verilog`。仅在新项目中可用 `--reference-file PATH` 授权 Planner 读取明确指定的资料；不会自动联网检索。

Human v2 成对测试使用同一数据集与模型配置，分别运行两次批量命令，传入相同 `--experiment-pair-id` 和不同 `--orchestrator legacy|langgraph`；报告文件会分开保存。若没有 golden TB，新入口返回 `generated_tb_pass_await_manual` 或 `generated_tb_failed_await_manual`，不能视为基准通过。LangSmith 追踪需自行配置 `LANGSMITH_TRACING=true` 和 `LANGSMITH_API_KEY`；默认关闭。Web 工作台见[现有说明](reference/WEB_WORKBENCH_ZH.md)。
