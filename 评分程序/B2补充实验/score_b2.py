from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / 'evaluation'))
import normalize_run_output as normalize
from reason_normalization import normalize_reason_text

ROOTS = ('ACTIVITY_MISSING', 'POLLUTANT_PARAMETER_MISSING', 'SOURCE_RELATION_MISSING', 'HARD_CONSTRAINT_CONFLICT')
POLLUTANTS = ('SO2', 'NOx', 'CO', 'VOC', 'PM10', 'PM2.5', 'BC', 'OC', 'NH3')
PATTERNS = {
    'ACTIVITY_MISSING': r'ACTIVITY_(?:OR_UNIT_)?MISSING|NH3_COAL_ACTIVITY_MISSING|FUEL_(?:AMOUNT|CONSUMPTION)_MISSING|MISSING_(?:ACTIVITY|FUEL_AMOUNT|FUEL_CONSUMPTION)|NO_ACTIVITY|NO_FUEL_RECORD|(?:活动水平|燃料消耗量|燃料用量).{0,16}(?:缺失|为空)|缺少.{0,8}(?:活动水平|燃料消耗量)',
    'POLLUTANT_PARAMETER_MISSING': r'FACTOR_INPUT_MISSING|POLLUTANT_PARAMETER_MISSING|PARAM_S_OR_A_MISSING|(?:SULFUR|ASH|FUEL_PARAM|COAL_PARAM)(?:ETER)?S?_MISSING|MISSING_(?:SULFUR|ASH|FUEL_PARAM)|MB_PARAM_UNAVAILABLE|(?:硫分|灰分|含硫量|物料衡算参数).{0,16}(?:缺失|为空|未提供)|缺少.{0,8}(?:硫分|灰分|含硫量)',
    'SOURCE_RELATION_MISSING': r'SOURCE_RELATION_MISSING|OUTLET_(?:LINK_|ID_)?MISSING|MISSING_OUTLET|(?:排放口|排口).{0,16}(?:缺失|为空|无法关联)|缺少.{0,8}(?:排放口|排口)',
    'HARD_CONSTRAINT_CONFLICT': r'HARD_CONSTRAINT_CONFLICT|(?:ACTIVITY|ASH|SULFUR)_OUT_OF_RANGE|PARAM_A_INVALID|(?:ACTIVITY|FUEL_AMOUNT|FUEL_CONSUMPTION|ASH|SULFUR)_NEGATIVE|NEGATIVE_(?:ACTIVITY|FUEL_AMOUNT|FUEL_CONSUMPTION)|(?:活动水平|消耗量|用量|灰分|硫分).{0,16}(?:负|小于.?0|非法|异常)',
}


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()} for row in rows)


def root_matches(text):
    text = normalize_reason_text(text)
    return {root for root, pattern in PATTERNS.items() if re.search(pattern, text, re.I)}


def load_outputs(run, manifest):
    if (run / 'outputs/baseline_inventory_items.csv').exists():
        items = normalize.baseline_rows(run, manifest)
    elif (run / 'outputs/generic_inventory_items.csv').exists():
        items = normalize.generic_rows(run, manifest)
    elif (run / 'outputs/source_pollutant_totals.csv').exists():
        items = read_csv(run / 'outputs/source_pollutant_totals.csv')
        for item in items:
            item['pollutant'] = normalize.POLLUTANT.get(item['pollutant'], item['pollutant'])
    else:
        items = []
    by_source = defaultdict(list)
    for item in items:
        by_source[item['source_id']].append(item)
    exceptions = defaultdict(list)
    path = run / 'outputs/generic_exception_list.csv'
    if path.exists():
        for row in read_csv(path):
            exceptions[row['source_id']].append(row)
    return by_source, exceptions


def source_roots(records, exceptions):
    roots = set()
    for item in records:
        roots |= root_matches(str(item.get('reason_codes', '')))
    for exc in exceptions:
        roots |= root_matches(';'.join(str(exc.get(k) or '') for k in ('root_cause', 'reason_code')))
    return roots


def same_output(before, after):
    if before['status'] != after['status']:
        return False
    for field in ('generation_t', 'emission_t'):
        a, b = before.get(field), after.get(field)
        if a in ('', None) or b in ('', None):
            if not (a in ('', None) and b in ('', None)):
                return False
        elif str(a).strip() == str(b).strip():
            continue
        elif not (normalize.is_number(a) and normalize.is_number(b)):
            return False
        elif not (math.isfinite(float(a)) and math.isfinite(float(b)) and
                  abs(float(a)-float(b)) <= max(1e-6, abs(float(a))*1e-6)):
            return False
    return True


def compare_to_baseline(label, records, predicted_roots, baseline_records, baseline_roots):
    """Describe paired output changes separately from absolute correctness.

    Call only with a baseline produced by the same workflow. This comparison
    measures observed changes; it does not establish causal identification.
    """
    result = dict(baseline_complete=False, baseline_preexisting_alert=None,
                  new_predicted_roots=[], new_blocked_pollutants=[],
                  preexisting_target_reason=None, unaffected_preserved=None,
                  incremental_root_recalled=None, incremental_targeted_success=None,
                  incremental_false_alarm=None)
    if baseline_records is None:
        return result
    before = defaultdict(list)
    after = defaultdict(list)
    for row in baseline_records:before[row['pollutant']].append(row)
    for row in records:after[row['pollutant']].append(row)
    if any(len(before[p]) != 1 for p in POLLUTANTS):
        return result
    blocked = ('information_insufficient', 'source_data_invalid')
    old_blocked = {p for p in POLLUTANTS if before[p][0]['status'] in blocked}
    new_blocked = {p for p in POLLUTANTS if len(after[p]) == 1 and after[p][0]['status'] in blocked} - old_blocked
    new_roots = predicted_roots - baseline_roots
    affected = set(label['affected_pollutants'])
    expected = label['root_cause']
    preexisting = expected != 'NO_ANOMALY' and expected in baseline_roots
    unaffected_ok = all(len(after[p]) == 1 and same_output(before[p][0], after[p][0])
                        for p in POLLUTANTS if p not in affected)
    affected_ok = all(len(after[p]) == 1 and after[p][0]['status'] in blocked and
                      all(after[p][0].get(k) in ('', None) for k in ('generation_t', 'emission_t'))
                      for p in affected)
    found = expected in new_roots
    result.update(baseline_complete=True,
                  baseline_preexisting_alert=bool(old_blocked or baseline_roots),
                  new_predicted_roots=sorted(new_roots), new_blocked_pollutants=sorted(new_blocked),
                  preexisting_target_reason=preexisting, unaffected_preserved=unaffected_ok,
                  incremental_root_recalled=None if preexisting else int(found),
                  incremental_targeted_success=None if preexisting else int(found and affected_ok and unaffected_ok),
                  incremental_false_alarm=int(bool(new_roots or new_blocked)))
    return result


def run_scores(run, output_root=HERE/'results', baseline_run=None):
    manifest = json.loads((run / 'run_manifest.json').read_text())
    labels = [r for r in json.loads((HERE / 'adjudication/objects.json').read_text()) if r['source_class'] == manifest['target']]
    base = {(r['source_id'], r['pollutant']): r for r in read_csv(HERE / 'adjudication/baseline_pollutants.csv')}
    by_source, exceptions = load_outputs(run, manifest)
    baseline_sources = baseline_exceptions = None
    baseline_id = ''
    if baseline_run is not None:
        baseline_manifest = json.loads((baseline_run / 'run_manifest.json').read_text())
        if (baseline_manifest['target'] != manifest['target'] or
                baseline_manifest['experiment_method'] != manifest['experiment_method'] or
                baseline_manifest.get('input_variant') != 'B0'):
            raise ValueError('Paired baseline must be B0 from the same method and source class')
        baseline_sources, baseline_exceptions = load_outputs(baseline_run, baseline_manifest)
        baseline_id = baseline_manifest['run_id']
    results = []
    for label in labels:
        sid = label['source_id']
        records = by_source[sid]
        by_p = defaultdict(list)
        predicted_roots = source_roots(records, exceptions[sid])
        for item in records:
            p = item['pollutant']
            by_p[p].append(item)
        expected = label['root_cause']
        affected = set(label['affected_pollutants'])
        found = expected in predicted_roots
        correct = []
        missing = []
        for p in POLLUTANTS:
            choices = by_p[p]
            if len(choices) != 1:
                correct.append(False)
                missing.append(p)
                continue
            item = choices[0]
            if p in affected:
                correct.append(item['status'] in ('information_insufficient','source_data_invalid') and
                               item['generation_t'] in ('', None) and item['emission_t'] in ('', None))
            else:
                prior = base[sid, p]
                status_ok = item['status'] == prior['expected_status']
                if prior['expected_status'] == 'calculated':
                    numeric_present = all(normalize.is_number(item[k]) and math.isfinite(float(item[k])) and float(item[k]) >= 0 for k in ('generation_t','emission_t'))
                    correct.append(status_ok and numeric_present)
                else:
                    correct.append(status_ok and item['generation_t'] in ('',None) and item['emission_t'] in ('',None))
        alert = bool(predicted_roots) or any(r['status'] in ('information_insufficient','source_data_invalid') for r in records)
        comparison = compare_to_baseline(
            label, records, predicted_roots,
            baseline_sources[sid] if baseline_sources is not None else None,
            source_roots(baseline_sources[sid], baseline_exceptions[sid]) if baseline_sources is not None else set(),
        )
        results.append(dict(run_id=manifest['run_id'], method=manifest['experiment_method'],
            source_class=manifest['target'], source_id=sid, entity_id=label['entity_id'],
            expected_root=expected, predicted_roots=sorted(predicted_roots), root_recalled=int(found),
            targeted_success=int(found and all(correct)), false_alarm=int(alert),
            missing_pollutants=missing, complete_output=int(not missing),
            normal_disposition_success=int(all(correct) and not alert),
            absolute_evaluation_basis='reference', baseline_run_id=baseline_id,
            **comparison))
    out = output_root / manifest['run_id']
    write_csv(out / 'objects.csv', results)
    return results


def macro_f1(records):
    values = []
    for root in ROOTS:
        tp = sum(r['expected_root'] == root and root in r['predicted_roots'] for r in records)
        fp = sum(r['expected_root'] != root and root in r['predicted_roots'] for r in records)
        fn = sum(r['expected_root'] == root and root not in r['predicted_roots'] for r in records)
        values.append(2*tp / (2*tp+fp+fn) if 2*tp+fp+fn else 0)
    return sum(values)/4


def metric(records, name):
    if name.startswith('incremental_'):
        selected = [r for r in records if r['baseline_complete'] and not r['preexisting_target_reason']]
        if name == 'incremental_macro_f1':
            if not all(any(r['expected_root'] == root for r in selected) for root in ROOTS):
                return None
            return macro_f1([{**r, 'predicted_roots':r['new_predicted_roots']} for r in selected])
        controls = name == 'incremental_false_alarm_rate'
        selected = [r for r in selected if (r['expected_root']=='NO_ANOMALY') == controls]
        field = {'incremental_root_recall':'incremental_root_recalled',
                 'incremental_targeted_success_rate':'incremental_targeted_success',
                 'incremental_false_alarm_rate':'incremental_false_alarm'}[name]
        return sum(r[field] for r in selected)/len(selected) if selected else None
    if name == 'macro_f1':
        return macro_f1(records)
    controls = name == 'false_alarm_rate'
    subset = [r for r in records if (r['expected_root'] == 'NO_ANOMALY') == controls]
    field = {'root_recall':'root_recalled', 'targeted_success_rate':'targeted_success', 'false_alarm_rate':'false_alarm'}[name]
    return sum(r[field] for r in subset)/len(subset)


def wilson(k, n):
    z=1.959963984540054
    p=k/n
    d=1+z*z/n
    m=(p+z*z/(2*n))/d
    h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return max(0,m-h), min(1,m+h)


def bootstrap(records, name):
    if name.startswith('incremental_'):
        records = [r for r in records if r['baseline_complete'] and not r['preexisting_target_reason']]
    strata = defaultdict(lambda: defaultdict(list))
    for r in records:
        strata[r['expected_root']][r['source_id']].append(r)
    rng = random.Random(20260930)
    estimates = []
    for _ in range(2000):
        sample = []
        for sources in strata.values():
            ids = list(sources)
            for sid in rng.choices(ids,k=len(ids)):
                sample.extend(sources[sid])
        estimates.append(metric(sample,name))
    estimates.sort()
    return estimates[49], estimates[1949]


def summaries(records, group):
    output=[]
    runs=sorted({r['run_id'] for r in records})
    for name in ('macro_f1','root_recall','targeted_success_rate','false_alarm_rate',
                 'incremental_macro_f1','incremental_root_recall',
                 'incremental_targeted_success_rate','incremental_false_alarm_rate'):
        estimate=metric(records,name)
        incremental=name.startswith('incremental_')
        controls=name.endswith('false_alarm_rate')
        eligible=[r for r in records if r['baseline_complete'] and not r['preexisting_target_reason']] if incremental else records
        selected = eligible if name.endswith('macro_f1') else [r for r in eligible if (r['expected_root']=='NO_ANOMALY')==controls]
        n=len({r['source_id'] for r in selected})
        if estimate is None:
            lower=upper=None
        elif len(runs)==1 and not name.endswith('macro_f1'):
            lower,upper=wilson(round(estimate*n),n)
        else:
            lower,upper=bootstrap(records,name)
        output.append(dict(source_class=records[0]['source_class'],method=records[0]['method'],
            run=group, metric=name, estimate=estimate, ci95_lower=lower,ci95_upper=upper,
            n_sources=n,n_runs=len(runs),missing_source_outputs=sum(not r['complete_output'] for r in records),
            evaluation_basis='paired_change' if incremental else 'absolute_reference',
            total_sources=len({r['source_id'] for r in records}),
            paired_sources=len({r['source_id'] for r in records if r['baseline_complete']}),
            preexisting_target_sources=len({r['source_id'] for r in records if r['preexisting_target_reason']})))
    return output


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runs', type=Path, default=HERE/'runs')
    parser.add_argument('--output-dir', type=Path, default=HERE/'results')
    parser.add_argument('--baseline-map', type=Path,
                        help='JSON mapping B2 run IDs to explicitly paired, same-workflow B0 run directories')
    args=parser.parse_args()
    baselines=json.loads(args.baseline_map.read_text()) if args.baseline_map else {}
    all_records=[]
    per_run=[]
    for run in sorted(args.runs.iterdir()):
        if (run/'logs/execution_metrics.json').exists():
            baseline=Path(baselines[run.name]) if run.name in baselines else None
            records=run_scores(run, args.output_dir, baseline_run=baseline)
            all_records.extend(records)
            per_run.extend(summaries(records,run.name))
    groups=defaultdict(list)
    for r in all_records:
        groups[r['source_class'],r['method']].append(r)
    summary=[s for records in groups.values() for s in summaries(records,'mean')]
    write_csv(args.output_dir/'per_run.csv',per_run)
    write_csv(args.output_dir/'summary.csv',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
