# 04 · Agent、工具与沙盒

| 角色 | 当前状态 | M1 读写边界 |
|---|---|---|
| Planner | 已接旧入口 | 读需求和授权参考摘要；写冻结的 Design Contract；不得写 RTL/TB 或运行 EDA |
| Testbench Agent | RS 实验已用；通用入口待接 | 只读契约，写生成 TB；不看 DUT 与 hidden golden TB |
| RTL Coder | 已接旧入口 | 读契约和测试目标摘要；写 RTL；不看完整 TB，不运行 EDA |
| Verifier | 已接旧入口的确定性服务 | 内部读取 DUT、生成 TB、必要时 golden TB；通过 Docker 网关调用 EDA；不调用 LLM 或改写 RTL |
| Debugger | 已接旧入口 | 读契约、当前 RTL、精简失败反例和授权 Memory；写新 RTL 候选；不看 golden TB |

M1 工具调用需要有真实调用记录。Planner 的 `read_file`/`list_dir` 只访问允许的规格和参考资料；Debugger 的只读查询只返回受控日志摘要。工具注册按角色控制动作、路径和预算，模型没有原始 shell 或任意路径写权限。VCD 专用 `read_waveform_summary` 在 M2 才开放。

现有 [DockerEDARunner](../verilog_agent/services/candidate_evaluation.py) 执行 `docker run --rm`，禁网、只读根与输入挂载、限制 CPU/内存/PID/超时；容器内 [tool_wrapper.py](../tool_wrapper.py) 提供 `compile`、`lint`、`simulate`、`eval_quality`、`benchmark` 并返回统一 JSON。RTL/TB 和审计证据可以保存在宿主机；EDA 程序只在容器内运行。详细字段见[工具接口](TOOL_INTERFACE.md)。

LangSmith 是 M1 的追踪目标：记录 graph 节点、模型/工具调用、耗时和错误分类；敏感环境变量、隐藏 golden TB 正文与参考 RTL 不进入远程追踪。追踪关闭时主流程仍须可运行。
