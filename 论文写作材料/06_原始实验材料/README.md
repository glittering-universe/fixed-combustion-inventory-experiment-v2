# 原始实验材料

[下载A—D原始实验材料](https://github.com/glittering-universe/fixed-combustion-inventory-experiment-v2/releases/tag/raw-experiment-materials)

| 文件 | 内容 |
|---|---|
| `experiment-A.zip` | 端到端清单编制的原始结果和Agent会话 |
| `experiment-B.zip` | 输入扰动、缺失与冲突处置的原始结果和Agent会话 |
| `experiment-C.zip` | 各规模任务的原始结果和Agent会话 |
| `experiment-D.zip` | Full与模块消融的原始结果和Agent会话 |
| `experiment-B2-supplement.zip` | 300个源对象的14次运行结果、12份Agent会话及逐对象评价 |

解压后按运行编号查看。`运行索引.csv`对应运行编号、方法、源类和Hermes会话；`逐次评分.csv`对应仓库中的实验评价。

## 每次运行的材料

- `结果`：当次输出的Excel、CSV及结果说明；人工交付包含原始工作簿与按表头提取的读取结果。
- `聊天记录/session.jsonl`：Hermes当次导出的原始会话文件。
- `聊天记录/聊天记录.md`：同次会话的可读文本，展示系统提示、用户指令、助手回答、工具调用及返回内容。
- `计算过程`：原运行已记录的逐项核算过程。
- `运行记录`：原运行的耗时、资源使用及执行日志。

Hermes聊天记录对应Full、通用工具Agent和消融方法。确定性脚本与人工流程提供各自的结果及运行材料。具体会话和消息数量见运行索引。

人工原表、表头读取结果和当时的过程记录分别保留。实验A人工的任务条件，以及B1/B2人工交付的适用范围，按仓库“人工对照”说明解释。

材料存放在同一GitHub仓库。

## B1、B2人工补充材料

`B1_B2人工补充结果明细.zip`收录两类源的S1—S4和B2，共10组结果，每组包含源分类、污染物结果、异常清单及运行摘要。

`人工补充_B1评价.json`及两份同前缀CSV保留现行程序的原始评分，供追溯。人工B1保持率的正式报告状态为“待核定”，见结果比较表和人工结果说明。
