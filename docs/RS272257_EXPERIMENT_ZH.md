# RS(272,257) 首轮实验进展

日期：2026-10-14。

## 当前完成范围

这是完整 RS 译码器实验的 KES 中间里程碑，不是完整 FEC 已完成。

- `verilog_agent/benchmarks/rs_reference.py`：GF(2^10)、系统编码、15 个 syndrome、BM、统一 PE 状态更新的 RiBM、Chien/Forney 和纠正后校验的软件参考模型。
- `verilog_agent/benchmarks/rs_demo.py`：固定规格、160 组向量、独立 KES 检查器，以及复用现有 PlannerGeneratorShowcase 的试验入口。保留现有 Planner、Generator、静态门禁、验证服务和 Debugger；没有新增平行的修复循环。
- `verilog_agent/agents/testbench.py`：独立的 LLM TB 生成角色。输入冻结规格及公开的 8 组样例，不接收 DUT 源码；其输出不自动成为 golden oracle。
- 当前预算：每种算法一个候选、最多一次 Debugger 修复。生成的 TB 单独仿真，独立固定 TB 才是 RTL 功能验收依据。
- `RSRunner` 在仿真退出码之外检查正向完成标志，防止只执行 `$finish` 就被记为通过。

需求来源：上级目录 `rs_baselines/RS272257_AGENT_REQUIREMENT.md`；算法依据用户提供书籍印刷页 58–67。BM 对应 Algorithm 2；RiBM 对应图 4.4 的 3t+1 统一单元阵列。软件模型通过书中例 30/31 的确切系数测试。

## 已验证

- 9 个单元测试：书中 BM/RiBM 确切输出、有限域独立 log/exp 表交叉检查、编码/译码回环、TB Agent 输入输出合同及无效响应拒绝。
- 2 个 Docker 集成测试，每种算法各检查 4 个受控夹具：正确结果回放通过，错误系数拒绝，缺少 done 拒绝，提前 `$finish` 拒绝。
- 每个准备过程用同一批 160 组接收码字分别跑 BM/RiBM 软件译码，共 320 次回环。覆盖 0..7 符号错误、零/全一/递增/随机消息和边界位置。
- 这些测试证明参考模型和测试基础设施的已检查行为，不证明 LLM 已生成正确 RTL。回放夹具只用于验证检查器，不是实现 RS 的硬件，也不进入模型上下文。
- Ruff 静态检查通过。原有用户修改未重构。
- 加上已有 Planner/Generator/Debugger 单元回归，共 18 项通过。首次受限运行有 2 项因 Windows 临时路径 PermissionError 失败；使用新临时目录在允许的本地执行环境重跑后 18/18 通过。
- 最新准备产物位于 `runs/rs_bm_ready_20261014` 与 `runs/rs_ribm_ready_20261014`，包含规格、完整帧向量、KES 向量、固定 TB 和 hash 清单。

软件 oracle 尚未与外部成熟 RS 库完成整码独立交叉验证；因此当前信心标记 MEDIUM。完整译码器阶段还需输入停顿、帧协议、处理中复位和超纠错能力观察用例；当前 KES TB 尚不覆盖所有这些条件。

## 可复现入口

从 VerilogAgent 目录执行；每次使用新输出目录，入口拒绝覆盖旧证据。

```powershell
# 不联网，不调用模型；准备软件向量、规格、固定 TB、hash 清单
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.rs_demo --algorithm bm
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.rs_demo --algorithm ribm

# 单元测试
.\.venv\Scripts\python.exe -m pytest tests/unit/test_rs_reference.py tests/unit/test_rs_testbench_agent.py -q --basetemp=.pytest_tmp_rs_unit

# 本地 Docker 检查器测试，不调用 API
.\.venv\Scripts\python.exe -m pytest tests/integration/test_rs_harness.py --run-integration -q --basetemp=.pytest_tmp_rs_docker

# 已允许使用配置中的模型服务后，执行真实试验（会产生 API 费用）
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.rs_demo --algorithm bm --live
.\.venv\Scripts\python.exe -m verilog_agent.benchmarks.rs_demo --algorithm ribm --live
```

真实入口依次生成实验 TB，运行已有 Planner/RTL Generator/验证修复流程，然后单独运行生成的 TB。`showcase_summary.json` 的 verified_rtl 仅指固定 KES oracle 结果；`agent_tb_result.json` 单独表示模型 TB 的执行结果。两者不能互相替代，均不代表完整译码器或综合通过。

## 当前阻塞及下一步

真实试跑在进程启动前被自动审批拒绝。经只读配置检查，目标模型为 `gpt-5.6-terra`，API 主机为 `wecodex.lol`，Responses 接口；未显示或记录密钥。本次没有发出模型调用。
审批指出需确认 RS 规格及向量向此服务发送的授权。需要用户允许后再运行；禁止换入口绕过拒绝。

预计发给模型：工程需求和自行整理的数学规则、TB 生成的 8 组样例、Planner 输出、生成 RTL 及必要修复诊断。不会发送整本 PDF、项目密钥、固定 golden TB 源码或受控回放夹具。API 服务可能处理或保留这些数据，且请求使用已配置账户额度。

授权后先跑两种 KES 的真实生成与仿真，按实际失败定位缺口；随后扩展至完整串行译码器。用户正在安装 Vivado，本次未执行综合，未报告任何 FPGA 资源、实现后时序或功耗结果。
