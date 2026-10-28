# 04 · Agent与工具

| 组件 | 当前职责与权限 |
|---|---|
| Planner | 需求/参考摘要/冻结合同 → SpecIR、TaskTree；缺少关键语义先提问，不写RTL |
| Generator | 合同、任务树、允许的Memory → 候选；集成时只生成顶层，不读独立TB |
| TB Agent | 无独立TB时依据合同生成测试；不看DUT实现；测试本身不是oracle |
| Debugger | 依据观察选择诊断、当前RTL、分类Memory、综合报告、修复、提问或停止 |
| 执行器/Verifier | 校验动作与预算，运行工具，保存原始证据，裁剪反馈并判断结果 |

Planner v2合同包含clock_edge、reset_polarity/reset_kind、signal_timing、handshakes、parameters。
0周期钟沿输出表示同一采样沿NBA之后可见；未知语义不会由名称猜测。
非法合同允许自动纠正一次，仍不合法终止为PLANNER_OUTPUT_ERROR；拒绝输出保留证据。
Planner参考调查只对显式白名单文件开放，多轮读取有次数、行数和字符预算，不是联网RAG。

Icarus编译/仿真、Verilator lint/评分、Yosys benchmark通过DockerEDARunner调用本地TOOL，
不需要包装为MCP。Docker禁网、只读、非root，具有CPU/内存/PID及超时限制。
ToolResult统一记录action/status/category/metrics/errors；模型不能执行任意shell。

Vivado使用官方MCP Server。约束/脚本由执行器生成，模型只读结构化反馈或提出受控综合动作。
它是可选工具协议接入，不等于所有Agent动作都必须改用MCP。
当前没有自动加载AMD知识库、Ross Skills/Design Examples，也没有专门的波形摘要动作。

Debugger可定向检索REPAIR_STRATEGY、ERROR_PATTERN、PROJECT，读取源码绑定的综合报告，
或在功能检查有效且动作获准时请求run_synthesis。所有修复都创建新候选并重新验证。
