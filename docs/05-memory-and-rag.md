# 05 · Memory与RAG

长期经验默认存于`memory_data/`，每类一个可续写SQLite文件，按条目检索而非读入整库。

| 类型 | 内容及使用 |
|---|---|
| DESIGN_PATTERN | 经功能验证的设计经验，供Generator |
| ERROR_PATTERN | 观测到的错误，供Debugger；不冒充成功修复 |
| REPAIR_STRATEGY | 有证据的修复策略，供Debugger；另存RepairAttempt情景记录 |
| PROJECT | 受控整理的适用规则，供Generator/Debugger |
| HUMAN_INTERVENTION | 人工介入来源与证据，EVIDENCE_ONLY，不在线检索 |

当前使用SQLite FTS5/BM25、类型/角色/模块族/状态过滤、同题隔离和证据门禁。
`off`不读写，`read_only`只检索，`read_write`按验证门禁追加。
任务工作记忆是checkpoint中的当前候选、诊断和调查记录，不等于长期经验库。

确定性离线consolidation已有入口；异步队列、语义聚类和外部文档RAG未实现。
只读参考工具不等于已完成AMD知识库/向量检索；本地RS记忆和人工来源不随公开仓库发布。
五类存储与检索已实现，不等于五类知识均能自动产生或收益已被实验证明。

正式对照需冻结Memory快照、题目顺序和数据可见性，见[评测](06-evaluation.md)。
更多文件迁移和检索说明见[分类Memory](TYPED_MEMORY_ZH.md)。
