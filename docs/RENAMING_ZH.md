# RTLForge更名与历史兼容

更新：2026-10-28。正式产品、Python包、命令、启动器和公开仓库名使用RTLForge。
项目变量前缀为`RTLFORGE_`，模型接口标准变量如`OPENAI_API_KEY`保持原名。
GitHub地址为`https://github.com/MANTOKA/RTLForge`；旧仓库名由GitHub重定向。

Windows启动器由`tools/build-console.ps1`构建，不随源码提交二进制。
新用户使用`.env.example`创建私有配置，不需要旧目录或本机历史数据库。

历史任务JSON中的旧绝对路径不批量改写，避免破坏原始证据。
候选续跑读取器在旧路径不存在时，仅在当前任务目录找到同名、同hash候选后安全定位。
不能假定每台机器都保留旧目录junction；当前本机已不依赖该入口。
本地`runs/`、`memory_data/`及更名备份保留，不对外发布，也不因开源而删除。

旧Orchestrator仍有CLI/Web/对照调用，保留其代码是运行兼容需要，与产品名称无关。
历史报告中的旧名称属于原始记录，不作为当前产品入口。
