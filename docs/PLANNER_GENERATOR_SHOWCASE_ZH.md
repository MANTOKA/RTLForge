# Planner 与 Generator 逐阶段演示

## 1. 当前究竟接入了什么 API

项目实现的是 `OpenAIResponsesProvider` 适配器，使用 OpenAI Python SDK 的 Responses API
和严格 JSON Schema。它不是一个已经登录的模型账号，也没有内置 API Key。

当前有两种运行模式：

| 模式 | Provider | 是否调用 LLM | 能否证明模型理解了新需求 |
|---|---|---:|---:|
| 离线演示 | `ScriptedProvider` | 否 | 否，只用于观察完整过程 |
| 真实演示 | `OpenAIResponsesProvider` | 是，Planner 和 Generator 各一次 | 是 |

如果没有设置 `OPENAI_BASE_URL`，真实模式请求 `https://api.openai.com/v1`。如果设置了该
变量，则请求指定的 OpenAI-compatible 地址。兼容服务必须实现 Responses API 和严格 JSON
Schema；只有 Chat Completions 接口的服务目前不能直接使用。

Codex、ChatGPT 或 GitHub 的登录状态不会自动成为这个 Python 项目的 API Key。真实模式需要
单独配置 `OPENAI_API_KEY` 和 `VERILOG_AGENT_MODEL`。
OpenAI Key 的创建和 PowerShell 环境变量设置可参考
[OpenAI Developer Quickstart](https://developers.openai.com/api/docs/quickstart)。

## 2. 先运行完全免费的离线展示

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.agents.demo --offline-example
```

程序使用一个固定的 4 位计数器示例，终端依次显示：

1. 用户原始自然语言需求。
2. Planner 输出的模块名、端口、时钟、复位、工程假设和验收条件。
3. Planner 输出的层次 Task Tree。
4. Generator 的两个设计模式、完整 RTL、SHA-256 和文件路径。
5. Orchestrator 从 `CREATED` 到 `EVALUATING` 的事件轨迹。
6. 所有保存产物的位置。

离线结果会明确显示：

```text
Provider: scripted_offline_example (NO LLM API CALL)
verified_rtl: false
```

因此不能把离线结果写成“LLM 已经理解并生成”。它证明的是展示程序、Agent 输入输出合同、
领域模型转换、候选落盘和事件记录都能工作。

## 3. 配置真实模型

不要把真实 Key 写入 `demo.py`、发到聊天、GitHub Issue 或提交到仓库。推荐在仓库根目录
建立一个只保存在本机的 `.env`：

```powershell
Copy-Item .env.example .env
notepad .env
```

把 `.env` 编辑为：

```dotenv
OPENAI_API_KEY=替换为你的Key
VERILOG_AGENT_MODEL=替换为服务商提供的模型ID
OPENAI_BASE_URL=https://中转服务的基础地址/v1
```

`.env` 已被 `.gitignore` 忽略。真实演示会自动读取当前目录的 `.env`，但不会覆盖已经在
PowerShell 中设置的同名变量，也不会把 Key 写入 `runs/`、数据库或报告。不要修改并填写
`.env.example`，因为它是会被 Git 管理的公开模板。

中转平台的连接信息应这样理解：

| 中转字段 | 本项目配置 | 说明 |
|---|---|---|
| `key` | `OPENAI_API_KEY` | 只写入本机 `.env` |
| `url` | `OPENAI_BASE_URL` | 填基础地址；若给的是完整 `/responses` 地址，应去掉末尾 `/responses` |
| `type` | 不直接填写 | 用来判断接口兼容性，当前必须是 OpenAI Responses API 兼容类型 |
| 模型 ID | `VERILOG_AGENT_MODEL` | `type/key/url` 不能代替模型 ID，需向服务商查询 |

当前代码最终请求 `POST <OPENAI_BASE_URL>/responses`。若中转只支持
`POST .../chat/completions`，或者 `type` 是 Azure、Anthropic、Gemini，则不能直接使用当前
适配器，需要另加对应 Provider。仅写着“OpenAI compatible”也不一定包含 Responses API，
应查看中转平台文档或先询问其客服。

配置后可先执行不联网、不消耗额度的检查：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.agents.demo --check-config
```

它只显示模型、基础 URL、预期的 `/responses` 地址和 `api_key: configured (value hidden)`，
绝不会显示 Key。若 `.env` 放在其他位置，可使用
`--env-file D:\path\to\private.env`。

## 4. 手动输入需求并观察真实 Planner/Generator

执行：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.agents.demo
```

看到下面的提示后粘贴一行需求并按 Enter：

```text
请输入一行 RTL 自然语言需求，然后按 Enter：
>
```

建议先使用接口和时序都明确的需求。

### 示例 A：4 位计数器

```text
设计一个4位同步加法计数器模块 counter，输入 clk 和高有效同步复位 rst，输出 q[3:0]。rst=1 时在下一个时钟上升沿将 q 清零，否则 q 每个上升沿加1，溢出后回绕到0。
```

应重点检查 Planner 是否给出 `counter`、`clk/rst/q`、同步高有效复位和模 16 回绕；再比较
Generator 候选是否采用了不同但功能等价的组织方式。

### 示例 B：带使能的 8 位寄存器

```text
设计模块 reg8，输入 clk、同步高有效复位 rst、使能 en 和数据 d[7:0]，输出 q[7:0]。时钟上升沿到来时 rst 优先：rst=1 则 q 清零；否则 en=1 时 q 更新为 d；en=0 时保持原值。
```

应检查 Planner 是否把复位优先级和保持行为列入验收条件；Generator 不应添加或删除端口。

### 示例 C：允许重叠的 1011 序列检测器

```text
设计同步序列检测器 seq1011，输入 clk、同步高有效复位 rst 和串行输入 din，输出单周期脉冲 match。当最近输入比特形成 1011 时 match=1，允许重叠检测；其他周期 match=0。
```

这个例子适合观察 Generator 是否给出不同 FSM 组织，例如单进程状态机与状态寄存器/组合
转移分离的实现。

也可以把需求直接写在命令后：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.agents.demo `
  "设计模块 reg8，输入 clk、rst、en、d[7:0]，输出 q[7:0]……"
```

## 5. 结果保存在哪里

每次运行都会显示一个 `task_id`，并保存：

```text
runs/<task_id>/
├── input.txt
├── spec_ir.json
├── task_tree.json
├── showcase_summary.json
└── candidates/
    ├── cand_01/<module_name>.v
    └── cand_02/<module_name>.v
```

`showcase_summary.json` 包含 Provider 标签、阶段耗时、SpecIR、Task Tree、候选元数据和完整事件
序列。RTL 正文保存在与顶层模块同名的文件中，例如 `counter.v`。这样可以避免 Verilator
把文件名与模块名不一致报告为 `DECLFILENAME` lint 失败。

## 6. 当前结果能说明什么

真实模式能够说明：

- Planner 确实调用模型把新需求转换为结构化计划。
- Generator 确实调用模型生成不同 RTL 候选。
- 输出通过了 JSON Schema、本地域模型、模块名、重复代码和路径安全检查。

不带 `--static-evaluation` 的通用演示只证明规划和生成，仍会停在 `EVALUATING`，并写入
`verified_rtl=false`。这不是系统能力边界，而是该轻量展示入口没有提供用户 testbench。

对 Human v2 单题，使用 `--human-v2-root`、`--human-v2-case` 和 `--static-evaluation` 后，同一演示
会继续显示每个候选的接口检查、Docker 编译/Lint/StaticScore、golden-testbench verification、候选
回退，以及失败时的 Router/Debugger/复验。只有官方参考预检通过且 Agent 候选 `mismatch=0` 才会写入
`verified_rtl=true`；具体命令见 `HUMAN_V2_SINGLE_AND_BATCH_ZH.md`。
