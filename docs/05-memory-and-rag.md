# 05 · Memory 与外部文档 RAG

## 已实现的历史经验 Memory

[SQLiteMemoryStore](../verilog_agent/memory/store.py) 存储经验证的设计模式、错误观察和修复策略，使用 FTS5/BM25 检索，记录来源与验证证据。Orchestrator 现有 `off`、`read_only`、`read_write` 模式。离线 [Consolidation](../verilog_agent/memory/consolidate.py) 已有入口；完整五类记忆、异步调度和正式收益证明仍待完成。具体门禁与案例见[实现记录](MEMORY_IMPLEMENTATION_ZH.md)。

## M2 才启用的外部文档 RAG

AXI/APB 规范、RTL 编码规则和获准使用的 IP 资料属于外部知识库。M1 的 `doc_rag_enable=False`；M2 从本地预导入文档开始，保存标题、来源、版本、许可和片段位置，用 FTS5 做初版检索，测出收益后再考虑向量索引。外部文档片段不能直接写入“已验证修复”Memory，也不能混入 Human v2 题目的参考答案、`ref.sv` 或 golden TB。

每次检索记录来源和被哪个 Agent 使用。新旧编排对照使用同一只读 Memory 快照；`read_write` 组单独运行并固定题目顺序，避免前一任务写入影响后一任务的可比性。Memory 增益须通过[受控消融](06-evaluation.md)证明，不能只凭检索命中率判断。
