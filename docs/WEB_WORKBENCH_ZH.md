# VerilogAgent 本地 Web 工作台

第一版实现对应 [产品规格](WEB_UI_PRODUCT_SPEC_ZH.md) 与 [实施提示](WEB_UI_IMPLEMENTATION_PROMPT_ZH.md)。无需安装额外的 Web 框架；仍需按原项目要求配置 `.env`、数据集和 Docker 镜像，运行任务会调用真实 LLM，浏览历史不会。

在仓库根目录启动：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.web.server --port 8765
```

打开 `http://127.0.0.1:8765/`。服务只监听本机地址；关闭终端即停止服务。若使用其他 Python 环境，先按 README 安装项目依赖，再用该环境的 `python -m verilog_agent.web.server` 启动。

页面包括单任务、Human v2/RTLLM 批量测试、历史回放、共同题目对比与设置。任务启动使用固定参数白名单和受控 testbench 目录；一次只允许一个运行中的后台任务。批量任务完成或中断后可按原配置续跑；旧 CLI 报告可查看但不可直接作为 Web 任务续跑。服务重启会将尚标记 RUNNING 的 Web 任务显示为 INTERRUPTED，已保存的任务/报告仍能回放；这不代表原子进程一定被终止。

模型配置下拉框读取仓库根目录的 `.env` 和 `.env.<名称>`（例如 `.env.qwen`）。选择配置会同时切换该文件中的模型、API 地址、密钥和采样参数；网页只显示非秘密信息。手动编辑模型名只适用于所选配置对应的同一 Provider。不要在网页或报告中粘贴密钥。

每个配置文件应显式设置 API 协议。OpenAI Responses 兼容服务使用 `VERILOG_AGENT_API_STYLE=responses`；SiliconFlow 等 Chat Completions 服务使用 `VERILOG_AGENT_API_STYLE=chat_completions`。`auto` 仅用于兼容旧的 DashScope 配置，不建议新配置依赖域名推断。

Human v2／RTLLM 单题在调用 LLM 前先通过 Docker 做官方参考设计预检。若网页服务运行于无权执行 Docker 的沙箱，即使主机上 Docker Desktop 正常，也会被阻止；此时页面会显示 Docker 状态并禁用依赖 Docker 的启动按钮。请在具备 Docker 权限的本机终端运行上述服务命令。自然语言“只生成”不依赖 Docker，但仍会调用所选模型。

接口见 `/api/openapi.json`；任务和报告的 Markdown 可从详情页导出，JSON 可通过对应 `/api/tasks/{id}` 和 `/api/benchmarks/{id}` 获取。页面从 `runs/` 读取现有产物，不改写旧报告。历史没有记录的 Token、采样值、事件等显示“当时未记录”。功能通过率仅以 PASS/FAIL 为分母，ERROR（含额度耗尽）单列，原报告统计保留在 `stored_metrics`。这是 oracle-assisted closed-loop 测试，不称为严格 Pass@k。页面不提供 golden testbench/reference 正文或 API key。

历史列表的“运行时间”优先取单任务首条事件、批量报告 `created_at` 或网页任务启动时间，按浏览器本地时区显示到分钟，并可按起止日期筛选。没有这些记录的旧产物只显示“文件日期”，这是回退推定值，不等同于确切启动时间。

本地验证：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_web.py -q
.\.venv\Scripts\python.exe -m ruff check verilog_agent/web tests/unit/test_web.py
```

以上测试使用临时产物与假数据，不请求付费模型；真实 LLM、Docker、批量断点续跑仍需在具备额度和镜像的机器上进行端到端验收。
