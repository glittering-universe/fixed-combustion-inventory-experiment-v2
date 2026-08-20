# 固定燃烧源候选规则包 — 专业复核快照

- 状态：**待环境专业复核**（awaiting_professional_review）
- 包标识：`tcses-144-2024-fixed-combustion`（version 0.1.0-candidate）
- 生成时间：2026-08-07（阶段R会话，自动构建）
- 标准：T/CSES 144—2024《城市大气污染源排放清单编制技术指南》
- 标准SHA-256：`bc46346443e76ccf063ae5e2b1d1c5d7a8544fb4f7cf6b11239c80bca66181a9`

## 1. 标准范围

冻结范围（空白规则包规范）：通用核算方法与公式、第7章（电力热力源）、第8.3节（化石燃料固定燃烧）、第15章（质控及不确定性分析）、附录A、C、D、E.1、E.7。

PDF范围抽取工具返回40个物理页：3–15、29–55。抽取文本按页分隔，覆盖上述章节与附录（附录A 表A.1；附录C 表C.1–C.3；附录D 表D.1；附录E 表E.1（含烟气脱硝NH3逃逸行）、表E.7）。

## 2. 规则包构成

- 规则文件：5 个 YAML（scope / methods / formulas / controls / quality_control）
- 规则总数：34 条
  - scope 5 条、method 8 条、formula 11 条、control 5 条、quality_control 5 条
- 参数表：5 个 CSV
  - power_heat_emission_factors.csv：560 行（附录D 表D.1，70 行×8 污染物）
  - industrial_boiler_emission_factors.csv：192 行（附录E 表E.7，24 行×8 污染物）
  - coal_parameters.csv：14 行（附录C 表C.1/C.2/C.3，电力热力源10行＋工业源4行）
  - control_efficiencies.csv：518 行（附录A 表A.1，74 项技术×7 污染物列）
  - ammonia_slip_factors.csv：2 行（附录E 表E.1 烟气脱硝行，SNCR 0.17 / SCR 0.16 g/kg煤）
- 映射表：3 个 CSV（standard_fuels 22 项、standard_combustion_technologies 7 项、standard_control_technologies 74 项；只登记标准原词）
- 测试：20 条（tests/rule_tests.jsonl）

## 3. 关键公式（formulas.yaml）

| 编号 | 公式 | 来源页 |
|---|---|---|
| (1) | E = Σ C×Q×T×10⁻⁶（在线监测法） | p9 |
| (2) | E = A×EF×(1−η)（产排污系数法） | p9 |
| (3) | EF_SO2 = 2×S×(1−sr)（燃煤SO2物料衡算） | p10 |
| (4) | EF_PM = Aar×(1−ar)×fPM（燃煤颗粒物物料衡算） | p10 |
| (5) | EF_BC = EF_PM2.5×fBC | p10 |
| (6) | EF_OC = EF_PM2.5×fOC | p10 |
| (7) | Ed = (24/n)×Σ(Ci×Qi×10⁻⁶)，n≥18 | p10 |
| (8) | Qd = (24/n)×ΣQi，n≥18 | p10 |
| (9) | Ed = E×Qd/ΣQd（365或366天） | p10 |
| (10) | Ed = E×Ad/ΣAd（365或366天） | p10–11 |
| — | 电力生产NOx容量分档：≤100MW=8.96；(100,300)=8.19；≥300=7.21（表D.1 注b） | p45 |

## 4. 附录参数表

- 表A.1 控制措施平均去除效率（%）：SO2/NOx/VOCs/PM2.5/PM2.5-10/BC/OC 七列，74 项技术；注a 定义 PM2.5-10 为粒径>2.5且≤10μm；注b 溶剂使用两行为收集效率。
- 表C.1 硫分/灰分进入底灰比例；表C.2 粒径分布系数；表C.3 BC/OC占PM2.5比例。
- 表D.1 电力热力源污染物产生系数（g/kg燃料；注c 气体燃料 g/m3；注b 电力生产NOx容量分档）。
- 表E.1 工艺过程污染物产生系数（烟气脱硝行单位 g/kg煤，即NH3逃逸）。
- 表E.7 化石燃料固定燃烧污染物产生系数（g/kg燃料）。

## 5. 自动检查结果

`validate_fixed_combustion_rule_package`（2026-08-07 阶段R会话）：

- 结果：success = true，errors = 0，warnings = 0
- 规则条数：34（scope 5 / method 8 / formula 11 / control 5 / quality_control 5）
- 参数行数：1286（表D.1 560 + 表E.7 192 + 附录C 14 + 表A.1 518 + NH3逃逸 2）
- 映射行数：103（燃料22 + 燃烧技术7 + 控制技术74）
- 测试条数：20
- 标准PDF SHA-256 与 manifest 一致：`bc46346443e76ccf063ae5e2b1d1c5d7a8544fb4f7cf6b11239c80bca66181a9`
- 专业复核状态：not_performed（外部最终关口，本候选包未自行认定通过）

说明：本快照撰写时按候选包实际内容更新规则总数（34条）。覆盖率比对（compare_fixed_combustion_rule_coverage）依赖专业复核形成的必要规则清单，尚未执行，见"需专业确认事项"第6条。

## 6. 需专业确认事项

1. **表E.7 气体燃料单位**：E.7 表题为 g/kg 燃料，抽取文本未见D.1注c的"气体燃料单位g/m3"脚注（E.7中气体燃料行名带脚注标记a，但注a为"NOx以NO2计"）。候选包按表题将E.7全部行登记为 g/kg 燃料；D.1注c的g/m3仅适用于表D.1的6类标注c气体燃料。请专业复核E.7气体燃料行单位是否应与D.1一致为g/m3。
2. **燃料相态归类**：standard_fuels.csv 的 category（固体/液体/气体）按表D.1/E.7的燃烧技术列归组（煤粉炉/流化床炉/层燃炉行→固体；燃气锅炉行→气体；燃油锅炉行→液体；焦炭、煤矸石、其它焦化产品归固体）。该归类为表结构推导，标准未给出逐燃料相态表，请复核。
3. **生活源C表行排除**：表C.1–C.3 含生活源"民用化石燃料燃烧"行，超出第7章/第8.3节固定燃烧范围，coal_parameters.csv 未收录，请确认。
4. **表C.3缺行**：表C.3无"电力供应-煤粉炉"行，coal_parameters.csv 中该行 BC/OC 占PM2.5比例留空（unavailable），与标准未给出严格区分，未补0。
5. **表A.1全表收录**：control_efficiencies.csv 收录表A.1全部74项技术（含扬尘源、农业源、油气回收、溶剂使用等非固定燃烧措施），仅作为附录参数完整性保留；rules/controls.yaml 未声明其对固定燃烧源的适用性，适用性取舍留待专业复核。
6. **覆盖统计**：候选包与独立准备的必要规则清单的覆盖率比对（compare_fixed_combustion_rule_coverage）待专业复核形成所需规则清单后执行，本候选包未自行认定覆盖率或通过结论。
7. **溶剂使用-全部密闭/外部集气罩**两行VOCs列为收集效率（表A.1 注b），非去除效率，已按原值登记，未换算。
8. **电力生产NOx容量分档**适用于表D.1中标记"装机容量"的电力生产煤炭/煤矸石行（焦炭行为常数8.85），已按此登记。
