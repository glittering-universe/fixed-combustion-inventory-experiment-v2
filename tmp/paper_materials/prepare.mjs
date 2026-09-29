import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook } from '@oai/artifact-tool';

const root = '/Users/wushuo/Desktop/环境学院论文/固定燃烧源清单经验缺失处置实验';
const out = path.join(root, '论文写作材料');
const summary = path.join(root, '正式实验结果/00_评价汇总');
const read = async p => JSON.parse(await fs.readFile(p, 'utf8'));
const result = name => read(path.join(summary, `${name}_v2_2_human_v2_1.json`));
const method = {
  full: 'Full完整方法', deterministic_program: '简单确定性脚本',
  generic_tool_agent: '通用工具Agent', expert_led: '既有专家主导型流程',
  w_o_complete_trace: '去除完整过程记录',
  agent_managed_admission: 'Agent管理核算准入',
  w_o_executable_rule_gate: '去除可执行规则门控',
  w_o_calculation_item_structure: '去除污染物核算项结构',
};
const target = { INDUSTRIAL: '工业锅炉', POWER: '火电热力', EXCLUDE: '范围外' };
const classes = {
  ACTIVITY_MISSING: '活动水平缺失', POLLUTANT_PARAMETER_MISSING: '污染物参数缺失',
  SOURCE_RELATION_MISSING: '源关系缺失', HARD_CONSTRAINT_CONFLICT: '硬约束冲突', NO_ANOMALY: '正常对照',
};
const cell = v => v === null || v === undefined ? '' : String(v);
const quote = v => /[,"\r\n]/.test(cell(v)) ? `"${cell(v).replaceAll('"', '""')}"` : cell(v);

async function table(relative, headers, rows) {
  const wb = Workbook.create();
  const sheet = wb.worksheets.add('数据');
  const range = sheet.getRangeByIndexes(0, 0, rows.length + 1, headers.length);
  range.values = [headers, ...rows.map(r => r.map(v => v ?? null))];
  wb.recalculate();
  const csv = range.values.map(r => r.map(quote).join(',')).join('\n');
  const file = path.join(out, relative);
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.writeFile(file, '\uFEFF' + csv + '\n');
  console.log(`${relative}: ${rows.length}行`);
}

async function csvRows(file) {
  const text = (await fs.readFile(file, 'utf8')).replace(/^\uFEFF/, '');
  const wb = await Workbook.fromCSV(text, { sheetName: '原始数据' });
  const values = wb.worksheets.getItemAt(0).getUsedRange().values;
  const [headers, ...rows] = values;
  return rows.map(row => Object.fromEntries(headers.map((h, i) => [h, row[i]])));
}

async function copy(source, relative) {
  const dest = path.join(out, relative);
  await fs.mkdir(path.dirname(dest), { recursive: true });
  await fs.copyFile(source, dest);
}

const a = (await result('aggregate_summary')).experiment_A_EICPI_core;
await table('03_实验结果/A_方法比较.csv',
  ['方法', 'D常规', 'D异常', 'D', 'N', 'E', 'EICPI_core', '对照条件'],
  Object.entries(a.by_method_macro_and_micro).map(([m, x]) => [method[m], x.macro.D_regular, x.macro.D_exception,
    x.macro.D, x.macro.N, x.macro.E, x.macro.EICPI_core_0_100,
    m === 'expert_led' ? '历史业务流程对照；原始任务条件待确认' : '相同原始基表任务']));
await table('03_实验结果/A_分源类比较.csv',
  ['方法', '源类', '运行次数', '参照纳入源数', 'D常规', 'D异常', 'D', 'N', 'N最小值', 'N最大值', 'E', 'EICPI_core'],
  Object.values(a.by_method_target).map(x => [method[x.method], target[x.target], x.run_count, x.record_count,
    x.D.D_regular, x.D.D_exception, x.D.score, x.N.score, x.N.min, x.N.max, x.E, x.EICPI_core_0_100]));
await table('03_实验结果/效率计算基准.csv', ['源类', '人工单条耗时(s/条)', '脚本单条耗时(s/条)'],
  Object.entries(a.anchors).map(([t, x]) => [target[t], x.human_seconds_per_record, x.deterministic_seconds_per_record]));

const b1 = await result('experiment_B1');
await table('03_实验结果/B1_方法比较.csv', ['方法', '比较对数', '一致项数', '比较项数', '语义保持率'],
  Object.entries(b1.by_method).map(([m, x]) => [method[m], x.pair_count, x.passed_units, x.total_units, x.semantic_retention]));
await table('03_实验结果/B1_各类输入扰动.csv',
  ['方法', '源类', '扰动', '原输入运行', '扰动运行', '比较项数', '一致项数', '差异项数', '语义保持率', '差异字段分布'],
  b1.comparisons.map(x => [method[x.method], target[x.target], x.variant, x.baseline_run_id, x.perturbed_run_id,
    x.total_units, x.passed_units, x.failed_units, x.agreement_rate, JSON.stringify(x.mismatch_by_field)]));

const b2 = await result('experiment_B2');
await table('03_实验结果/B2_异常识别比较.csv', ['方法', '计分对象数', '五类宏平均F1', '分类准确率'],
  Object.entries(b2.five_class_macro_f1_by_method).map(([m, x]) => [method[m], x.eligible_records, x.macro_f1, x.accuracy]));
await table('03_实验结果/B2_分异常类别结果.csv',
  ['方法', '类别', '真实对象数', '预测对象数', '正确识别数', '误报数', '漏报数', '精确率', '召回率', 'F1'],
  Object.entries(b2.five_class_macro_f1_by_method).flatMap(([m, x]) => Object.entries(x.by_class).map(([c, v]) =>
    [method[m], classes[c], v.support, v.predicted, v.true_positive, v.false_positive, v.false_negative, v.precision, v.recall, v.f1])));
await table('03_实验结果/B2_分源类处置结果.csv',
  ['方法', '源类', '计分对象数', '正向注入数', '负对照数', '宏平均F1', '状态转换正确率', '注入根因召回率', '定向处置成功率', '负对照保持率'],
  b2.runs.filter(x => x.method !== 'expert_led').map(x => [method[x.method], target[x.target], x.eligible_records,
    x.positive_injections, x.negative_controls, x.five_class_classification.macro_f1,
    x.affected_disposition_transition.rate, x.injected_root_recall.rate, x.targeted_handling_success.rate,
    x.negative_control_unchanged_vs_method_B0.rate]));

const c = await result('experiment_C');
await table('03_实验结果/C_规模与耗时.csv',
  ['方法', '源类', '规模(%)', '运行次数', '单条耗时中位数(s/条)', '单条耗时最小值(s/条)', '单条耗时最大值(s/条)',
    '总耗时中位数(s)', '总耗时最小值(s)', '总耗时最大值(s)', '峰值内存中位数(bytes)'],
  c.entries.map(x => [method[x.method], target[x.target], x.scale_percent, x.run_count,
    x.seconds_per_record.median, x.seconds_per_record.min, x.seconds_per_record.max,
    x.wall_seconds.median, x.wall_seconds.min, x.wall_seconds.max, x.peak_rss_bytes.median]));

const d = await result('experiment_D');
await table('03_实验结果/D_模块消融.csv',
  ['方法', '源类', 'D常规', 'D异常', 'D', 'N', 'D相对Full变化', 'N相对Full变化', '过程记录覆盖率', '单条耗时(s/条)'],
  d.entries.map(x => [method[x.method], target[x.target], x.D.D_regular, x.D.D_exception, x.D.score,
    x.N.score, x.delta_D_vs_full, x.delta_N_vs_full, x.process_record_coverage, x.seconds_per_record]));

const runs = await csvRows(path.join(summary, 'run_scores_v2_2_human_v2_1.csv'));
const fields = ['experiment', 'run_id', 'method', 'target', 'input_variant', 'scale_percent', 'repetition',
  'D', 'D_regular', 'D_exception', 'D_exception_detection_f1', 'D_root_cause_macro_jaccard', 'D_exception_disposition_accuracy',
  'N', 'N_passed', 'N_applicable', 'N_NA', 'E', 'EICPI_core_0_100', 'record_count', 'seconds_per_record',
  'method_boundary_status', 'method_boundary_failures', 'method_boundary_warnings'];
await table('03_实验结果/全部运行记录.csv', [...fields, '结果用途'], runs.map(x => [...fields.map(k => x[k]),
  x.method === 'expert_led' ? (x.experiment === 'A' ? '历史业务流程对照' : '人工旧交付观察；当前扰动任务交付待补充') : '机器实验结果']));

if (process.argv.includes('--results-only')) process.exit(0);

const catalog = await csvRows(path.join(root, 'reference/frozen/v2.0.0/expected_sources.csv'));
await table('04_核算依据/源分类明细.csv', ['源编号', '源类', '年份', '行业代码', '行业名称', '设备类型', '排口编号', '核算项数', '分类理由'],
  catalog.filter(x => x.variant === 'B0').map(x => [x.source_id, target[x.expected_target], x.inventory_year, x.industry_code,
    x.industry_name, x.equipment_type, x.outlet_id, x.expected_item_count, x.source_gate_reasons]));
const exceptions = await csvRows(path.join(root, 'reference/frozen/v2.0.0/expected_exceptions.csv'));
const groups = new Map();
for (const x of exceptions) {
  const key = JSON.stringify([x.variant, x.target, x.canonical_root_cause, x.requires_user_judgment]);
  if (!groups.has(key)) groups.set(key, new Set());
  groups.get(key).add(x.source_id);
}
await table('04_核算依据/参照异常类别统计.csv', ['输入条件', '源类', '原因类别', '需要人工判断', '涉及源数'],
  [...groups].map(([k, ids]) => { const [v, t, cause, human] = JSON.parse(k); return [v, target[t], cause, human, ids.size]; }));

const parameters = {
  industrial_boiler_emission_factors: '工业锅炉产生系数', power_heat_emission_factors: '电力热力产生系数',
  coal_parameters: '燃煤物料衡算参数', control_efficiencies: '治理效率', ammonia_slip_factors: '氨逃逸系数',
};
for (const [src, name] of Object.entries(parameters)) await copy(path.join(root, `rules/frozen/v1.0.1/parameters/${src}.csv`), `04_核算依据/参数/${name}.csv`);
const maps = { source_fuel_aliases: '燃料别名', source_control_aliases: '治理技术别名', source_combustion_aliases: '燃烧技术别名',
  standard_fuels: '标准燃料', standard_control_technologies: '标准治理技术', standard_combustion_technologies: '标准燃烧技术' };
for (const [src, name] of Object.entries(maps)) await copy(path.join(root, `rules/frozen/v1.0.1/mappings/${src}.csv`), `04_核算依据/映射/${name}.csv`);
for (const [src, name] of Object.entries({scope:'源分类',methods:'核算方法',formulas:'计算公式',controls:'治理与氨逃逸',quality_control:'数据质量',operational:'实验核算约定'})) {
  const content = (await fs.readFile(path.join(root, `rules/frozen/v1.0.1/rules/${src}.yaml`), 'utf8')).replace(/v\d+(?:\.\d+)*/g, '');
  const dest = path.join(out, `04_核算依据/规则/${name}.yaml`);
  await fs.mkdir(path.dirname(dest), { recursive: true });
  await fs.writeFile(dest, content);
}
await copy(path.join(root, 'reference/frozen/v2.0.0/expected_aggregates.csv'), '04_核算依据/参照排放汇总.csv');
await copy(path.join(root, 'reference/frozen/v2.0.0/expected_injection_outcomes.csv'), '04_核算依据/B2对象与预期变化.csv');

const impact = path.join(root, '../审计报告/规则包与参照包逐条核查_20260907/受影响条目定位');
for (const [src, name] of [['01_涉及源记录_326条.csv','专业问题涉及源记录.csv'],['02_已量化数值变化_21项.csv','湿式成分诊断复算.csv'],
  ['03_NH3终态影响_187项.csv','NH3适用性条目.csv'],['04_治理关联待核_137条.csv','治理关联条目.csv']])
  await copy(path.join(impact, src), `04_核算依据/问题条目/${name}`);

const human = path.join(root, 'human_baseline/original_packages/实验结果/02_实验A_端到端清单编制');
await copy(path.join(human, '01_工业锅炉/04_既有专家主导型/结果/固定燃烧源-工业锅炉_调整.xlsx'), '05_人工对照/工业锅炉_人工原表.xlsx');
await copy(path.join(human, '02_火电热力/04_既有专家主导型/结果/固定燃烧源-火电、热力生产与供应_调整.xlsx'), '05_人工对照/火电热力_人工原表.xlsx');

const background = await fs.readFile(path.join(root, '../论文研究笔记/传统固定燃烧清单流程问题与文献证据.md'), 'utf8');
const references = new Map();
for (const line of background.split('\n').filter(line => /^- /.test(line) && /https:\/\//.test(line))) {
  const url = line.match(/https:\/\/[^>\s)]+/)[0];
  if (!references.has(url)) references.set(url, line.includes('*') ? '- ' + line.slice(line.indexOf('*')) : line);
}
await fs.mkdir(path.join(out, '01_研究背景'), {recursive:true});
await fs.writeFile(path.join(out, '01_研究背景/文献线索.md'), '# 文献线索\n\n以下条目摘自已有研究笔记，供引言与相关工作选材。\n\n' + [...references.values()].join('\n') + '\n');
