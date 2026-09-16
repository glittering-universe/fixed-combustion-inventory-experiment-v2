# 实验材料与复现入口

当前结果位于`正式实验结果/`，评价采用D/N/E v2.2.0，人工表头读取采用v2.1.0。

## 解压实验材料

在实验仓库内运行：

```bash
python3 reproduction/curate_results.py restore .
```

该命令将A—D原始运行、人工规范化结果、规则、参照和输入解压到当前实验目录。压缩包位于`正式实验结果/08_完整封存/`，格式为tar.zst。

## 重新评价

```bash
.venv/bin/python -B evaluation/aggregate_experiment_results_v2.py --reference-package reference/frozen/v2.0.0 --human-normalization-root human_baseline/normalized_v2_1
```

评价结果写入`实验结果/00_总汇总与索引/`，文件名包含`v2_2_human_v2_1`。

## 打包结果

```bash
python3 reproduction/curate_results.py pack
```

生成文件位于`tmp/release_upload/`：结果目录压缩包、目录说明和运行索引。完整运行压缩包保存在`正式实验结果/08_完整封存/`，作为独立下载附件。

## 实验执行代码

- `run_all_machine_experiments.sh`：完整机器实验入口。
- `run_one_machine_experiment.sh`：单次运行入口。
- `experiment_control/`：实验输入、任务配置与运行组织。
- `method_package/`与`plugin/`：方法配置及领域工具。
- `requirements.lock.txt`：Python依赖版本。

历史执行配置与当前评价定义分别保留，具体运行信息随原始实验材料保存。
