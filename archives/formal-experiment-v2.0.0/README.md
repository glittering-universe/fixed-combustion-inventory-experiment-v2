# 正式原始基表实验 v2.0.0 索引

本目录是 150 次正式运行的轻量 Git 索引，不重复保存 A—D 实体运行包。

- `formal_run_index.csv`：150 次运行的矩阵字段、物理目录、seal、manifest、耗时哈希和会话 ID。
- `hermes_session_index.csv`：94 次 Agent 运行与 Hermes Desktop 会话的一对一映射。
- `formal_archive_index.json`：实验、方法、汇总文件与复现清单的总索引。

实体结果按实验 A—D 分包存入私有 GitHub Release `formal-experiment-v2.0.0`。Release 同时包含 Hermes 正式会话数据库的一致性备份、Git bundle、LFS 对象、`SHA256SUMS` 和往返验证记录。密钥、`.env`、模型缓存和临时虚拟环境不进入归档。
