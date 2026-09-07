# Human v2 单题演示与批量评测

本项目提供两个相互独立、但复用同一执行管线的入口：

1. 单题全过程 Demo：用于观察 Planner、Generator、Candidate Evaluation、Verification、
   Reflection Router 和 Debugger。
2. Human v2 批量评测：用于运行 1、10 或全部 156 题，并持续生成 JSON 和 Markdown 报告。

## 1. 数据文件在哪里

本机 Human v2 数据目录为：

```text
D:\AI\VerilogMulti\VerilogCoder-main\hardware_agent\examples\VerilogCoder\verilog-eval-v2\dataset_dumpall
```

每道题有三个文件：

- `<problem_id>_prompt.txt`：自然语言需求；仅此文件可以进入 Planner/Generator。
- `<problem_id>_test.sv`：golden testbench；只交给确定性 Verification。
- `<problem_id>_ref.sv`：官方参考 RTL；只作为仿真黑盒和工具链预检，不进入任何 LLM 上下文。

可以直接查看 prompt：

```powershell
Get-Content "D:\AI\VerilogMulti\VerilogCoder-main\hardware_agent\examples\VerilogCoder\verilog-eval-v2\dataset_dumpall\Prob035_count1to10_prompt.txt"
```

也可以用记事本打开：

```powershell
notepad "D:\AI\VerilogMulti\VerilogCoder-main\hardware_agent\examples\VerilogCoder\verilog-eval-v2\dataset_dumpall\Prob035_count1to10_prompt.txt"
```

## 2. 单题全过程 Demo

先进入项目根目录：

```powershell
cd D:\AI\VerilogMulti\VerilogAgent
```

运行一题，并允许最多一次 Debugger 修复：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.agents.demo `
  --human-v2-root "D:\AI\VerilogMulti\VerilogCoder-main\hardware_agent\examples\VerilogCoder\verilog-eval-v2\dataset_dumpall" `
  --human-v2-case Prob155_lemmings4 `
  --candidates 2 `
  --static-evaluation `
  --max-repairs 2 `
  --docker-image verilog-agent-sandbox:reflection
```

该命令会真实调用 `.env` 中配置的模型。终端按顺序显示：

```text
prompt 与官方参考预检
  -> Planner / SpecIR / Task Tree
  -> Generator 候选 RTL
  -> Candidate Evaluation（接口、编译、Lint、静态评分）
  -> Verification（golden testbench）
  -> 若失败：Reflection Router -> Debugger -> 重新评分与仿真
  -> Agent RTL 与官方参考 RTL 的最终对比
```

模型可能第一次就生成正确代码，因此某次运行不一定触发 Debugger。这是正常实验结果，不能为了演示
而把自然失败伪装成固定失败。需要稳定展示修复链时，应使用已标记为 defect injection 的演示。

### PASS 判据

Human v2 testbench 同时实例化 Agent 的 `TopModule` 和官方 `RefModule`，逐采样比较输出：

- 官方参考在当前工具链预检 PASS；
- Agent RTL 编译和仿真 PASS；
- `mismatch=0`。

三项都满足才算功能通过。官方参考若发生 Icarus 不兼容，应记录为工具异常，而不是 Agent 失败。

## 3. 批量评测

先用一题检查配置和报告：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.human_v2_batch `
  --dataset-root "D:\AI\VerilogMulti\VerilogCoder-main\hardware_agent\examples\VerilogCoder\verilog-eval-v2\dataset_dumpall" `
  --limit 1 `
  --candidates 2 `
  --max-repairs 2 `
  --output runs\human-v2\gpt-5.6-terra\reports\human_v2_smoke.json
```

开发阶段运行前 10 题：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.human_v2_batch `
  --dataset-root "D:\AI\VerilogMulti\VerilogCoder-main\hardware_agent\examples\VerilogCoder\verilog-eval-v2\dataset_dumpall" `
  --limit 10 `
  --candidates 2 `
  --max-repairs 2 `
  --output runs\human-v2\gpt-5.6-terra\reports\human_v2_dev10.json
```

完整运行 156 题时删除 `--limit`。程序每完成一题都会重写 JSON 和 Markdown 报告；若中断，使用完全
相同的配置和输出路径并添加 `--resume`，已记录的题不会重新调用模型。

只重跑旧报告中的可修复失败题，并使用最多 3 轮修复：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.human_v2_batch `
  --dataset-root "D:\AI\VerilogMulti\VerilogCoder-main\hardware_agent\examples\VerilogCoder\verilog-eval-v2\dataset_dumpall" `
  --rerun-failures-from runs\human-v2\gpt-5.6-terra\reports\human_v2_dev10.json `
  --candidates 2 `
  --max-repairs 3 `
  --output runs\human-v2\gpt-5.6-terra\reports\human_v2_dev10_functional_retry.json
```

该模式会重跑 `FUNCTIONAL_MISMATCH`，也会读取旧任务摘要，将“候选缺少 Human v2 要求的
`TopModule`”这一类旧 `SYSTEM_ERROR` 纳入重跑；新版会先强制正确模块名，并将仍存在的该问题判为
可修复的 `INTERFACE_MISMATCH`。它不会重跑 `TOOL_INCOMPATIBILITY` 或真正的 Docker/API
`SYSTEM_ERROR`，因为这些问题不应交给 Debugger 修改 RTL。

需要同时记录修复轨迹并让后续不同题目检索已验证修复经验时，可在上述命令加入：

```powershell
  --memory-mode read_write `
  --memory-database runs\human-v2\gpt-5.6-terra\state\verilog_agent_memory.sqlite
```

`off` 是正式无记忆基线的默认值；`read_only` 只检索不写入；`read_write` 会保存所有修复尝试，
但只有最终通过 Verification 的修复才会形成可检索条目。同一个 Human v2 problem 不会检索自身经验，
Memory 中也不保存 prompt、完整 RTL、golden testbench 或官方参考 RTL。成功经验的摘要不再额外调用
一次 LLM，而是对已经由 Debugger 生成、且经过功能验证的 rationale 做确定性整理。

报告包含：

- 当前模型、候选数、最大修复次数、Docker 镜像和评测协议；
- 可评测题数、PASS/FAIL、通过率和官方参考工具异常数；
- 每题失败类别、简要原因、修复次数和验证循环次数；
- 哪些题是经过 Debugger 后才通过；
- 每题对应的 task ID 和完整 `showcase_summary.json`。

## 4. 不同模型对比

保持题集、候选数、修复预算、Docker 镜像和 endpoint 不变，只修改 `--model` 与输出文件：

```powershell
# 模型 A
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.human_v2_batch `
  --dataset-root "D:\AI\VerilogMulti\VerilogCoder-main\hardware_agent\examples\VerilogCoder\verilog-eval-v2\dataset_dumpall" `
  --limit 10 --candidates 2 --max-repairs 2 `
  --model "模型A的实际API模型ID" `
  --output runs\human-v2\model-a\reports\human_v2_batch.json

# 模型 B
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.human_v2_batch `
  --dataset-root "D:\AI\VerilogMulti\VerilogCoder-main\hardware_agent\examples\VerilogCoder\verilog-eval-v2\dataset_dumpall" `
  --limit 10 --candidates 2 --max-repairs 2 `
  --model "模型B的实际API模型ID" `
  --output runs\human-v2\model-b\reports\human_v2_batch.json
```

两个运行都完成后生成配对比较报告：

```powershell
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.human_v2_compare `
  runs\human-v2\model-a\reports\human_v2_batch.json `
  runs\human-v2\model-b\reports\human_v2_batch.json `
  --output runs\human-v2\model-comparisons.md
```

比较器要求两份报告使用相同题目顺序、候选数、修复预算、Docker 镜像和评测协议，否则拒绝给出
“模型能力差异”结论。报告给出通过率百分点差异，以及 A 通过/B 失败和 B 通过/A 失败的具体题目。

`VerilogCoder` 和 `MAGE` 是 Agent 系统名称，不是可以直接填入 `--model` 的模型 ID。它们的代码
分别配置 GPT、Claude、Llama 等基础模型，并使用不同的 prompt、工具和循环策略。公平比较需要分开：

1. 在本项目中通过 `--model` 比较相同管线下不同基础模型的能力。
2. 再为 VerilogCoder/MAGE 增加协议适配器，在相同题集和预算下比较完整 Agent 系统。

当前 provider 要求 endpoint 支持 OpenAI Responses API 和严格 JSON Schema。若某个本地模型服务只
支持 Chat Completions，必须新增 provider adapter，不能只把系统名称写进 `--model`。
