# 交互式 PowerShell 运行窗口

更新：2026-10-28，0.3.0。首次安装见[快速开始](02-getting-started.md)，
源码仓库不提交exe，执行`tools/build-console.ps1`可在Windows本机构建。
菜单5为通用模块集成，公开示例位于`examples/integration/`。
本版本完成范围及未完成目标见[验收矩阵](STATUS_ZH.md)。

Vivado MCP 综合设置、菜单 4 的已有候选综合，以及综合修复闭环见
[Vivado MCP 接入说明](VIVADO_MCP_ZH.md)。

在项目根目录双击 `RTLForge.exe`。exe 会打开 PowerShell 并调用
`.venv/Scripts/python.exe -m rtlforge.console`，无需输入 demo 命令。
exe、`launch-console.ps1`、项目源码和 `.venv` 必须保留在一起；本机需安装并启动 Docker。
它是本机启动器，不包含 Python、Docker 或 EDA 镜像。

1. 选择“输入需求并运行”，输入 txt 路径，或者输入多行需求后单独输入 `/run`。
2. 输入 TB 文件路径，或留空。提供文件时只使用该 TB，跳过 TB Agent；
   留空时只使用 TB Agent。TB 顶层需符合现有 Icarus 验证接口（通常为 `tb`）。
3. 每次运行前确认配置；输入 E 修改 candidates、max_repairs、memory_mode、env_file、镜像。
   配置保存在 `work/console_settings.json`，不保存密钥正文。明确选择的 env 文件覆盖同名进程变量。
4. 开始后逐阶段显示结果和文件位置；需要澄清时在原窗口回答。
   `/pause` 保留任务暂停状态及证据；菜单 3 可以从已有候选创建续跑分支，
   不等同于恢复到原 LangGraph 节点。

默认候选上限 2、修复上限 3、Memory read_write。模型调用会消耗配置服务的额度。
独立初稿静态失败时可以按候选上限再生成；进入修复链后，修复版编译/lint 失败
优先继续 Debugger。修复无进展时回滚后使用剩余修复预算；修复预算耗尽则以
`BUDGET_EXHAUSTED` 结束，不再生成独立候选。基础设施错误仍单独终止，不计作 RTL 修复失败。
第一次运行前可在设置中指定实际存在的私有 env 文件，例如 `.env.ds`。

证据位于 `runs/console/<时间>/<task_id>/`：`input.txt`、`spec_ir.json`、
`task_tree.json`、`context.json`、`events.json`、`model_calls.jsonl`、候选 RTL、
静态检查和仿真证据。TB Agent 路径另有 `generated_tb.sv`；用户 TB 在运行根目录保存快照。
模型调用记录为现有脱敏遥测，不包含完整提示词或思维链。

Planner 合同允许已声明握手的 request、acknowledge 和 data_signals 使用握手时序参考，
例如 start/done 不必伪装成数据信号。无关联的信号仍不能使用该参考。
若返回的结构化合同校验失败，程序保存拒绝原因和原始结构化 Plan，并允许 Planner
依据原需求自动纠正一次；不自动补造未知时序，也不消耗 RTL 修复预算。
控制台证据目录中的 `planner_validation_failures.json` 记录这些拒绝输出。
第二次仍不合法时，以 `PLANNER_OUTPUT_ERROR` 终止，不进入 TB/RTL 生成。

只通过生成 TB 时显示“生成测试通过，未经独立验证”，保留底层人工复核状态，
不会自动作出人工确认。用户 TB 通过只表示通过该测试，不代表测试覆盖全部行为。

重新构建启动器：在 PowerShell 执行 `./tools/build-console.ps1`。
构建使用 Windows 自带 .NET Framework C# 编译器，不需要安装打包工具。

## 从已有失败候选续跑

选择菜单 **3**，选择最近的任务编号，也可输入任务目录、`context.json` 或候选 RTL 路径。
选择 `cand_repair_01` 等完整候选，Enter 复用原任务提供的 TB；没有独立 TB 时复用历史 Agent TB。
确认本轮新增修复预算（0–3，通常选 3）及模型配置后开始。可以在预算耗尽后再次续跑。

续跑从候选重新静态检查/仿真开始，不重新调用 Planner、Generator 或 TB Agent。
每轮是一个新的任务与检查点分支，导入候选名为 `cand_seed_01`，避免覆盖本轮
`cand_repair_01`。`context.json` 的 `metadata.continuation` 保存原任务、原候选、
源文件 hash、旧预算及本轮预算；原文件不修改。后续比较必须计入各轮累计成本，
不能把人工追加预算后的成功当成原预算内成功。

## 复用已有模块生成顶层（通用集成）

菜单 **5** 接收新顶层需求、独立 TB 路径，以及已有模块清单 JSON；清单留空时逐个选择
最近任务编号或任务目录/context.json/候选 RTL 路径，再选择候选 ID。
此入口不限译码器；任意任务都可组合不同模块。每个候选需合同完整、静态分数至少75、
对应的独立TB通过。程序展示每个模块的 SpecIR、TaskTree、来源和 SHA256 后再确认运行。

清单格式如下，source_context 相对清单所在目录解析，也可使用绝对路径：

```json
{"modules": [{"source_context": "../console/<run>/<task>/context.json",
              "candidate_id": "cand_01"}]}
```

Planner/Generator/Debugger 接收冻结模块的合同与任务树，不能读取独立 TB/oracle。
Planner 只规划新顶层，Generator 只生成顶层文件；已有模块在新任务的 dependencies/ 下保存
快照与 hash。静态检查拒绝遗漏实例或重定义子模块；仿真和 MCP 综合读入所有源码。
Debugger 只修复顶层。菜单3续跑和菜单4重新综合均继承冻结依赖，历史源码不修改。
若选中的候选本身已集成子模块，也会复制其完整冻结依赖；只要求新顶层直接实例化
本次选择的模块。共享同名同 hash 依赖合并，不同实现的同名依赖拒绝合并。
若必须修复子模块，应单独修复验证后，再选新的模块候选发起集成。

本机完整BM案例输入在忽略发布的 `runs/rs-decoder/`；
公开可操作示例见[通用集成指南](02-getting-started.md#通用模块集成示例)。

## 单次输出截断

默认 32768 token，可在设置中单独指定；留用 env 时读取配置文件的同名值。
Responses（含流式）明确返回 `max_output_tokens` 或 Chat Completions 返回 `length` 时，
窗口保存 `output_limit/attempt_NNN/partial_response.txt` 和决策记录，并询问是否提高上限。
Enter 将当前上限翻倍，也可输入更大的正整数；N 停止。重试会重新生成当前步骤的
完整 JSON，不拼接不完整 RTL，不重新执行已经完成的 Agent 阶段，不额外扣修复次数。
每次请求分别记录模型调用及 token 遥测；上限提高只对当前运行生效。
服务端可能拒绝超过模型/中转硬上限的请求；省略上限不代表无限生成。
