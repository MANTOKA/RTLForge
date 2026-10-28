# 06 · 评测与结论边界

分开报告软件回归、真实模型案例、独立功能测试、综合结果和研究效果消融。
脚本化provider或测试MCP Server的成功不能写成真实模型/Vivado成功。

新旧编排对照应固定模型、题目、预算、75分阈值、镜像、oracle可见性和只读Memory快照。
前置TB、Debugger动作选择、候选预算和评分×Memory均是额外变量；需要独立控制。
Human v2已有legacy/langgraph选择；本轮最终版本未完成全量成对重测，历史报告仍是历史报告。

当前控制台：提供独立TB时跳过TB Agent；未提供时生成测试只算辅助检查，最终需人工复核。
使用隐藏golden反馈进行修复属于oracle-assisted closed loop，不能直接称为严格spec-only Pass@1。

必记字段：每候选源码/依赖hash、SpecIR、约束、静态分、仿真结果、综合状态、终止原因、
LLM与工具调用数、token/耗时、修复谱系、Memory开关与人工追加预算。
provider截断/配额、工具不兼容和许可证问题应单列，不混入RTL功能失败。

本地完整BM案例通过160帧与OOC综合，细节见[当前状态](STATUS_ZH.md)；
不证明评分、Memory或LangGraph提高了总体正确率，也不等于穷尽RTL行为验证。
新版本对照、外部RAG及波形工具等后续工作仍需讨论，本次发布不补跑付费大规模评测。
