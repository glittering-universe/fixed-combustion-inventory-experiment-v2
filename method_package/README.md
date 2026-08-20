# 冻结方法包

本目录保存正式实验启动时使用的最小方法配置。权威业务规则位于
`rules/frozen/v1.0.1/`；本目录只保存 Agent 运行说明、场景装配关系和
输入适配版本，不复制排放因子或治理参数。

正式运行前执行 `experiment_control/freeze_method_packages.py`，生成
`frozen_manifest.json` 和各方法的 SHA-256。运行清单只接受该清单中的
方法哈希。

简单确定性脚本基线位于 `baseline/simple_deterministic/`，使用自身冻结的
固定映射和参数快照直接计算，不加载 Full 的规则包或 Plugin。
