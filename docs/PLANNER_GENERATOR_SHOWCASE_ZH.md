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

不要把真实 Key 发到聊天、GitHub Issue 或提交到仓库。在你自己的 PowerShell 窗口临时设置：

```powershell
$env:OPENAI_API_KEY = "替换为你的API-Key"
$env:VERILOG_AGENT_MODEL = "替换为你账户可用的模型ID"

# 只有使用其他兼容服务时才设置：
# $env:OPENAI_BASE_URL = "https://兼容服务地址/v1"
```

环境变量只对当前 PowerShell 窗口有效。项目不会把 Key 写入 `runs/`、数据库或报告。

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

当前仍不能说明候选功能正确，因为 Candidate Evaluation 和 EDA Verification 尚未接入该演示。
程序因此停在 `EVALUATING`，并写入 `verified_rtl=false`。下一阶段将自动显示每个候选的接口检查、
lint/compile/quality 评分和淘汰原因；再下一阶段才执行仿真、分类修复和最终 RTL 验证。
