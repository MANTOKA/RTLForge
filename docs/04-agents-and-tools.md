# 04 · Agent、工具与沙盒

| 角色 | 当前状态 | M1 读写边界 |
|---|---|---|
| Planner | 已接旧入口 | 读需求和授权参考摘要；写冻结的 Design Contract；不得写 RTL/TB 或运行 EDA |
| Testbench Agent | 已接 M1 新入口 | 只读契约，写生成 TB；不看 DUT 与 hidden golden TB |
| RTL Coder | 已接旧入口 | 读契约和测试目标摘要；写 RTL；不看完整 TB，不运行 EDA |
| Verifier | 已接旧入口的确定性服务 | 内部读取 DUT、生成 TB、必要时 golden TB；通过 Docker 网关调用 EDA；不调用 LLM 或改写 RTL |
| Debugger | 已接旧入口 | 读契约、当前 RTL、精简失败反例和授权 Memory；写新 RTL 候选；不看 golden TB |

M1 的 Planner 在用户显式传入 `--reference-file` 时，可请求一次 `read_file` 或 `list_dir`；工具层只读取白名单文件、截断长内容并记录调用。没有授权文件时不发生工具调用。Debugger 仍只消费现有 Verifier 提供的结构化摘要，尚未提供模型自主读取日志的工具。模型没有原始 shell 或任意路径写权限。VCD 专用 `read_waveform_summary` 在 M2 才开放。

现有 [DockerEDARunner](../verilog_agent/services/candidate_evaluation.py) 执行 `docker run --rm`，禁网、只读根与输入挂载、限制 CPU/内存/PID/超时；容器内 [tool_wrapper.py](../tool_wrapper.py) 提供 `compile`、`lint`、`simulate`、`eval_quality`、`benchmark` 并返回统一 JSON。RTL/TB 和审计证据可以保存在宿主机；EDA 程序只在容器内运行。详细字段见[工具接口](reference/TOOL_INTERFACE.md)。

设置 `LANGSMITH_TRACING=true` 与密钥后，LangGraph 节点、结构化模型调用和 Planner 只读工具会进入 LangSmith 追踪；默认关闭。模型提示词可能包含用户需求、授权参考片段或候选 RTL，开启前应确认可上传；隐藏 golden TB 正文与参考 RTL 仍不传给模型。
