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


def run_scores(run, output_root=HERE/'results'):
    manifest = json.loads((run / 'run_manifest.json').read_text())
    labels = [r for r in json.loads((HERE / 'adjudication/objects.json').read_text()) if r['source_class'] == manifest['target']]
    base = {(r['source_id'], r['pollutant']): r for r in read_csv(HERE / 'adjudication/baseline_pollutants.csv')}
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
    results = []
    for label in labels:
        sid = label['source_id']
        records = by_source[sid]
        by_p = defaultdict(list)
        predicted_roots = set()
        for item in records:
            p = item['pollutant']
            by_p[p].append(item)
            reason = str(item['reason_codes'])
            roots = root_matches(reason)
            predicted_roots |= roots
        for exc in exceptions[sid]:
            text = ';'.join(str(exc.get(k) or '') for k in ('root_cause', 'reason_code'))
            roots = root_matches(text)
            predicted_roots |= roots
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
        results.append(dict(run_id=manifest['run_id'], method=manifest['experiment_method'],
            source_class=manifest['target'], source_id=sid, entity_id=label['entity_id'],
            expected_root=expected, predicted_roots=sorted(predicted_roots), root_recalled=int(found),
            targeted_success=int(found and all(correct)), false_alarm=int(alert),
            missing_pollutants=missing, complete_output=int(not missing),
            normal_disposition_success=int(all(correct) and not alert)))
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
    for name in ('macro_f1','root_recall','targeted_success_rate','false_alarm_rate'):
        estimate=metric(records,name)
        controls=name=='false_alarm_rate'
        selected = records if name=='macro_f1' else [r for r in records if (r['expected_root']=='NO_ANOMALY')==controls]
        n=len({r['source_id'] for r in selected})
        if len(runs)==1 and name!='macro_f1':
            lower,upper=wilson(round(estimate*n),n)
        else:
            lower,upper=bootstrap(records,name)
        output.append(dict(source_class=records[0]['source_class'],method=records[0]['method'],
            run=group, metric=name, estimate=estimate, ci95_lower=lower,ci95_upper=upper,
            n_sources=n,n_runs=len(runs),missing_source_outputs=sum(not r['complete_output'] for r in records)))
    return output


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runs', type=Path, default=HERE/'runs')
    parser.add_argument('--output-dir', type=Path, default=HERE/'results')
    args=parser.parse_args()
    all_records=[]
    per_run=[]
    for run in sorted(args.runs.iterdir()):
        if (run/'logs/execution_metrics.json').exists():
            records=run_scores(run, args.output_dir)
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
