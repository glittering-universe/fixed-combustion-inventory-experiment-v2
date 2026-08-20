# 阶段 R 任务

使用 `fixed-combustion-inventory:formalize-fixed-combustion-rules` Skill。

输入：

- 原始PDF：`/Users/wushuo/Desktop/环境学院论文/1 城市大气污染源排放清单编制技术指南 T_CSES 144-2024.pdf`
- 空白规范：`/Users/wushuo/Desktop/环境学院论文/固定燃烧源清单经验缺失处置实验/rules/stage_r/空白规则包规范.md`
- PDF抽取文本：`/Users/wushuo/Desktop/环境学院论文/固定燃烧源清单经验缺失处置实验/rules/stage_r/tcses_fixed_combustion_scope.md`
- 候选输出：`/Users/wushuo/Desktop/环境学院论文/固定燃烧源清单经验缺失处置实验/rules/stage_r/candidate`
- 自动检查报告：`/Users/wushuo/Desktop/环境学院论文/固定燃烧源清单经验缺失处置实验/rules/stage_r/candidate_validation.json`

边界：只能读取原始PDF、抽取文本和空白规范；不得搜索或读取工作区内任何既有规则、计算答案、计算工作簿、论文结果或参照标签。先调用PDF范围抽取工具，再形成候选包并调用规则包校验工具。覆盖统计等专业复核形成所需规则清单后再执行，本会话不得伪造该清单或把候选包标记为已通过。

