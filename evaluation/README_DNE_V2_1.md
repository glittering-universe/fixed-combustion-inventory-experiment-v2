# D/N/E 正式评价 v2.1

v2.1 是实验完成后的评价协议修订，替代 v2.0，不修改任何参评运行、封存输出、核算工作簿或冻结参照包。修订原因是 v2.0 将所有专业决策原子直接池化，大量常规核算项会稀释少量但关键的异常识别与处置错误。

该修订改变了方法的综合排序：v2.0 中简单确定性程序因速度优势取得最高综合分，v2.1 中完整方法取得最高综合分。因此，v2.0 结果必须作为敏感性结果保留，v2.1 不得被表述为实验前预注册指标。

## 核心指标

EICPI 的三个维度和权重不变：

\[
\mathrm{EICPI}_{\mathrm{core}}
=100(0.35D+0.35N+0.30E).
\]

`N` 和 `E` 保持 v2.0 口径。`D` 改为两个一级分量的等权组合：

\[
D=\operatorname{mean}_{\mathrm{available}}
(D_{\mathrm{regular}},D_{\mathrm{exception}}).
\]

- `D_regular`：常规核算的源类归属、核算终态、核算依据和污染物相关治理参数判断。已纳入异常源联合处置的终态原子从该分量剔除，避免重复计分。
- `D_exception`：以需要用户作出专业判断的异常源为计分单位。仅当异常根因集合完全一致，且该源所有受影响污染物的终态处置均正确时通过。分母是预期和实际异常源的并集，因此误报同样计错。

当某个评价范围无预期异常且无实际异常时，`D_exception=NA`，`D` 退化为 `D_regular`；仅有实际误报时，`D_exception=0`。

同时报告异常源检出 F1、根因集合宏平均 Jaccard 和异常终态处置准确率。v2.0 的原子微平均 D 仅作敏感性诊断，不进入 `D` 或 EICPI。

`D_exception` 解决的是常规核算项与异常源之间的数量失衡，并不对不同异常根因类别再次等权；各根因类别的样本数仍可能不均衡。因此，论文需同时报告 B2 五分类结果和自然异常的分根因诊断，不得仅以 `D_exception` 概括所有异常类型的泛化能力。

## 重复与源类聚合

- 同一方法和源类的重复运行：分别取 `D_regular` 和 `D_exception` 的中位数，再重建层级 D。
- 工业锅炉与火电热力：先分别对两个 D 分量做源类等权平均，再合成 D。
- 为避免恢复类别失衡，不再提供按全部 D 原子数池化的替代排名；表中的 macro 和 micro D 均明确等于该层级合成值。

## B2 受控异常评价

B2 不使用整体 D/N 作为异常能力结论。对 26 个合格受控对象，将同一方法的 B2 与 B0 比较，以“新增根因+受影响项处置变化”形成五类预测：

1. `ACTIVITY_MISSING`；
2. `POLLUTANT_PARAMETER_MISSING`；
3. `SOURCE_RELATION_MISSING`；
4. `HARD_CONSTRAINT_CONFLICT`；
5. `NO_ANOMALY`。

按五个类别一对其余计算 Precision、Recall 和 F1，并对五个 F1 等权取宏平均。4 个注入前已存在问题的对象不进入分母。整体参照 D/N 仅作诊断，B2 不进入 EICPI。

## 输出

v2.1 不覆盖 v2 文件，默认生成：

- `run_scores_v2_1.csv`；
- `aggregate_summary_v2_1.json`；
- `experiment_B1_v2_1.json`；
- `experiment_B2_v2_1.json`；
- `experiment_C_v2_1.json`；
- `experiment_D_v2_1.json`。
