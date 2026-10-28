# 02 · 安装与使用

需要Python3.10+（本轮测试3.12）、Git、可运行Linux容器的Docker。
Vivado综合可选；没有Vivado也能生成和仿真。

## Windows PowerShell

```powershell
git clone https://github.com/MANTOKA/RTLForge.git
cd RTLForge
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,graph,llm,mcp]"
docker build -t rtlforge-sandbox:latest .
Copy-Item .env.example .env
```

在`.env`填写自己的密钥和模型。Responses兼容端点用`RTLFORGE_API_STYLE=responses`；
Chat Completions端点用`chat_completions`。兼容地址填写`OPENAI_BASE_URL`。
模型必须支持本项目的JSON输出要求；服务端输出限制不等于本地可无限提高。

```powershell
.\.venv\Scripts\python.exe -m rtlforge.console
```

双击入口由`powershell -ExecutionPolicy Bypass -File tools/build-console.ps1`构建，
生成的`RTLForge.exe`需要与源码、`launch-console.ps1`和`.venv`一起使用。
Linux/macOS使用虚拟环境对应的`python -m rtlforge.console`。

## 单需求示例

选择1，输入`examples/counter/requirement.txt`的本机绝对路径；
TB输入`examples/counter/golden_tb.sv`的绝对路径。
这些是项目编写的独立需求和检查器，不依赖下载基准数据。
确认候选数、修复次数、Memory、env文件和综合开关；不具备Vivado环境时关闭综合。

窗口逐阶段报告结果及位置。Planner可能针对需求缺项提问；回答后继续。
输入文字需求也可以，最后单独输入`/run`；`/back`返回。
有独立TB时只使用该TB；留空则由TB Agent生成，结果明确标注未经独立验证。

## 通用模块集成示例

先让上述counter通过独立TB，然后选择5：

1. 新顶层需求：`examples/integration/requirement.txt`的绝对路径。
2. 独立TB：`examples/integration/golden_tb.sv`的绝对路径。
3. 模块清单留空，选择刚才counter任务和成功候选；不再添加模块。
4. 查看接口、时钟复位、合同来源，确认配置运行。

程序读取已有SpecIR/TaskTree，冻结源码和传递依赖。Planner设计新顶层合同，Generator仅生成
counter_wrapper，所有源码一起编译、仿真和可选综合；Debugger只修顶层。
选择复用候选要求合同完整、静态分>=75且具有对应独立TB通过证据。
这不是自动拆分并生成所有子模块；没有现成子模块时先分别生成验证。

重复使用相同组合时可提供清单JSON，路径相对清单文件解析：

```json
{"modules": [{"source_context": "../console/<run>/<task>/context.json", "candidate_id": "cand_01"}]}
```

## 续跑、综合与证据

- 菜单2统一修改配置，每次运行前仍确认。
- 菜单3选已有候选，新建分支并追加修复预算；重新验证，不重跑Planner/初稿生成。
- 菜单4不调用LLM，重新静态/独立仿真/综合已有候选。
- 截断时选择更大输出预算重试该步骤；每次模型请求分别计费记录。

证据在`runs/console/<时间>/<task_id>/`：`input.txt`、`spec_ir.json`、`task_tree.json`、
`context.json`、`events.json`、`model_calls.jsonl`、`candidates/`、`tool_runs/`、`verification.json`。
集成额外保存`integration_manifest.json`和`dependencies/<模块名>/`的源码/合同/任务树。
综合保存候选目录中的`synthesis/attempt_*/result.json`、Tcl、XDC、报告与DCP。

Vivado需要自行安装和配置有效许可证，再下载官方MCP Server；在菜单2填写绝对路径、
器件、时钟和周期。用户可在需求中说明设计目标，Planner提取合同，程序对缺少的综合输入补问。
未配置综合时，功能完成不代表综合通过。详见[Vivado MCP](VIVADO_MCP_ZH.md)。

## 离线与批量

```powershell
.\.venv\Scripts\python.exe -m rtlforge.benchmarks.single_case_demo --offline-example
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest --run-integration -m integration
```

离线脚本使用固定模型替身，不请求真实服务。Docker集成测试需镜像和引擎。
Web仍可通过`python -m rtlforge.web.server --port 8765`在本机启动；它不具有全部控制台新能力。
Human v2/RTLLM需单独下载数据，见[基准指南](reference/HUMAN_V2_SINGLE_AND_BATCH_ZH.md)。
不将旧基准成绩直接视为本版本成绩；全量成对重测和消融尚未完成。
