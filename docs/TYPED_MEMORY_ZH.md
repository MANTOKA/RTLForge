# 按五类文件保存长期 Memory

更新：2026-10-28，五类存储与定向检索已实现；异步队列、外部文档RAG和有效性消融未完成。
下述RS条目是本地案例，数据库、源码和任务证据不分发。新克隆默认从自己的空库开始。
原项目书完成程度见[验收矩阵](STATUS_ZH.md)。

默认目录为 `memory_data/`，每种类型一个持续追加的 SQLite 文件，按条目去重，
不按每次任务建新长期库。使用索引查询，Agent 不需要把整个文件读取到上下文。

| 文件 | 内容 | 在线使用 |
|---|---|---|
| DESIGN_PATTERN.sqlite | 已验证设计模式 | Generator |
| ERROR_PATTERN.sqlite | 观测到的错误模式 | Debugger |
| REPAIR_STRATEGY.sqlite | 已验证修复策略；另表存 RepairAttempt 情景记录 | Debugger |
| PROJECT.sqlite | 受控整理的项目规则 | Generator / Debugger |
| HUMAN_INTERVENTION.sqlite | 人工或 Codex 直接改写的来源与证据 | EVIDENCE_ONLY，不在线检索 |

`DEFAULT_MEMORY_DATABASE` 现在指向目录。原来显式传 `.sqlite` 文件的评测脚本仍支持
单库；传目录即使用分类库。迁移不修改历史 benchmark 隔离库。
`off` 不读写；`read_only` 检索但不追加业务记忆；`read_write` 检索并按验证门禁追加。

Debugger 除兼容的 `retrieve_memory` 外，可自主选择三个定向动作：
`retrieve_repair_strategies`、`retrieve_error_patterns`、`retrieve_project_rules`。
它们只查询选定类型库；每次返回最多三条短摘要，ACTIVE/模块族/同题隔离规则继续生效。
Generator 的检索只允许 DESIGN_PATTERN/PROJECT，不会读取 REPAIR_STRATEGY。
仍保留既有的诊断阶段自动检索；定向动作供 Debugger 进一步选择类别。

迁移入口：`python -m rtlforge.memory.split --source <旧库> --directory memory_data`。
先用 SQLite backup 生成完整备份，再按原类型复制条目，保留状态、支持次数、
置信度和使用计数。12 条历史 RepairAttempt 迁入修复库的情景表，不提升为新策略。
`migration_report.json` 记录数量和备份位置；核对通过后可把旧混合库归档到 backups。

本次 RS BM cand_repair_03 仅整理为一条 REPAIR_STRATEGY：限定 rs_kes_bm 的
TIMEOUT/SIMULATION_TIMEOUT，供 Debugger 诊断参考。保存联合修改与 160 项测试通过的事实，
不将模型的根因猜测写成定论，不含完整 RTL、黄金 TB 或期望向量，不生成 PROJECT 条目。
证据文件仍在 runs，长期条目保存路径与 hash；不要删除其来源证据。

默认仍需以 read_only/read_write 运行才能利用新经验；Memory off 不会检索它。
