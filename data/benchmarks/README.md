# 本地 Benchmark 数据目录

此目录保存体积较大的外部 benchmark 数据，不属于产品源码，也不会提交到 Git。

```text
data/benchmarks/
├── verilog-eval-human-v2/   # NVlabs VerilogEval Human v2，156 题
└── rtllm-2.1/               # hkust-zhiyao/RTLLM 的固定 2.1 快照，50 题
```

每次正式评测前应记录数据来源 URL、获取日期、Git commit/tag、许可证和文件哈希。LLM Agent 只能读取
每题的自然语言需求；testbench 与参考 RTL 仅供确定性 Verification 使用，不能被注入 Prompt、Debugger
或可检索 Memory。

当前本机快照：

| 数据集 | 来源 | 固定版本 | 本机目录 |
|---|---|---|---|
| VerilogEval-Human v2 | `NVlabs/verilog-eval` 导出的 `dataset_dumpall` | 本地导入快照；正式实验前补记上游 commit | `verilog-eval-human-v2/` |
| RTLLM | `hkust-zhiyao/RTLLM` | commit `51ed553d0ffd32797a1a0a13e051656bf302c81f`，v2.1，2026-08-16 | `rtllm-2.1/` |

`tests/fixtures/` 只保留小型、人工挑选且随源码版本控制的回归夹具；不要把完整 benchmark 放回
`tests/`。
