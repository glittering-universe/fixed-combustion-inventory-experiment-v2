# 固定燃烧源清单实验复现入口 v2.1

> 本文保留原实验执行设置。当前评价已升级为D/N/E v2.2.0并使用人工表头读取v2.1.0，当前入口见仓库README和“正式实验结果”。清理后的完整运行目录保存在压缩包中，先运行`python3 reproduction/curate_results.py restore`恢复，再执行：
>
> `.venv/bin/python -B evaluation/aggregate_experiment_results_v2.py --reference-package reference/frozen/v2.0.0 --human-normalization-root human_baseline/normalized_v2_1`
>
> 历史v2.1协议、设置锁和下述旧入口保留用于追溯，不代表当前评价版本。

本目录是原始基表输入版 A—D 实验的唯一复现入口。依赖版本冻结在 `requirements.lock.txt`；`bootstrap_environment.sh` 使用本机 Python 创建仓库内 `.venv`，后续命令统一使用 `.venv/bin/python`。

## 冻结边界

- 输入：1 个基102设备/燃料表、4 个基101企业主表分片、1 个基101治理设施表，共 6 个原始结构脱敏工作簿；
- 候选范围：基102的 5,922 条中性 `SRC-RAW-*` 记录；A/B/D 不预先给出目标成员，C 仅给出中性嵌套抽样 ID；
- 扰动：`inputs/experiment_b/S1`—`S4` 与 `B2`，每个版本都包含 6 个工作簿、中性 ID 索引和版本清单；
- 人工基线：`human_baseline/original_packages/` 只读保存原始工作簿，`human_baseline/normalized_v2/` 提供规范化副本。复现脚本只把规范化 CSV/JSON 复制到物理结果路径，不覆盖原始包；
- 参照包：`reference/frozen/v2.0.0`；
- 评价：`evaluation/aggregate_experiment_results_v2.py`，按 D/N/E v2.1 与 `EICPI_core` 汇总。实验前 `metric_spec_v2.json` 保留在正式设置锁中；实验后活动协议为 `metric_spec_v2_1.json`。
- 归档索引：`reproduction/build_formal_archive_index.py`，仅在 150 个运行全部封存且汇总完成后生成运行哈希与 94 个 Hermes 会话的轻量索引。

## 运行前检查

```bash
./reproduction/bootstrap_environment.sh
./.venv/bin/python reproduction/check_environment.py
```

bootstrap 只在 `.venv` 不存在时创建环境并按锁文件安装依赖。检查器随后只读核对：正式设置锁、复现依赖清单、6个原始输入及其哈希、5类 B 变体、5,922 个中性 ID、方法包、规则包、参照包 v2、D/N/E 指标定义、人工 v2 索引、项目 `.venv`、Hermes Profile 及已安装 Plugin。任一哈希不一致都停止运行。

## 继续当前目录

```bash
./reproduction/run_all_machine_experiments.sh --resume
```

脚本先将 14 个人工 v2 包放到物理结果路径；已完整存在时跳过，部分存在时停止，不覆盖。然后按 A、B、C、D 执行 136 次机器运行，最后调用 D/N/E v2.1 汇总。

## 在新目录完整复现

```bash
./reproduction/run_all_machine_experiments.sh \
  --fresh-dir /Users/wushuo/Desktop/环境学院论文/固定燃烧源清单复现实验
```

目标目录必须为空。脚本复制冻结代码、输入、参照、人工原始包与规范化包，但不复制旧实验结果、Hermes 会话、控制器日志、评价结果或本机 `.venv`；随后依据锁文件在目标目录新建虚拟环境。

## 单次机器运行

```bash
./reproduction/run_one_machine_experiment.sh C-100-IND-FULL-R1 [实验根目录]
```

`RUN_ID` 必须存在于 v2 冻结矩阵。单次入口不会更改方法、比例、扰动版本或重复编号。

## 只重新汇总

```bash
./reproduction/aggregate_results.sh [实验根目录]
```

汇总器只读封存运行和人工 v2 包，生成 `run_scores_v2_1.csv`、`aggregate_summary_v2_1.json` 及 B1/B2/C/D v2.1 独立诊断报告；不会补跑或回填。

## 完整性清单

`reproduction/reproduction_manifest.json` 记录复现入口的全部执行依赖与 SHA-256，`matrix/formal_setup_lock.json` 记录实验输入、B 变体、人工 v2 包、方法包、规则包、参照包和评价契约。任何修改都必须重新冻结为新版本。
