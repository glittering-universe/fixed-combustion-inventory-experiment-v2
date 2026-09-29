import csv
import json
from pathlib import Path
from build_dataset import HERE, REFERENCE, table, dump

labels=json.loads((HERE/'adjudication/objects.json').read_text())
by_id={r['source_id']:r for r in labels}
evidence=[]
slip_sources=set()
parameter_sources=set()
with (REFERENCE/'expected_items.csv').open(encoding='utf-8-sig',newline='') as f:
    for item in csv.DictReader(f):
        sid=item['source_id']
        if item['variant']!='B0' or sid not in by_id:
            continue
        label=by_id[sid]
        reasons=json.loads(item['expected_reason_codes'])
        if item['pollutant']=='NH3' and 'NH3_SCR_SNCR_SLIP' in reasons:
            slip_sources.add(sid)
        if label['root_cause']=='POLLUTANT_PARAMETER_MISSING' and item['pollutant'] in label['affected_pollutants']:
            activities=json.loads(item['expected_activity_record'])
            if not any(a.get('slot')=='燃料'+label['fuel_slot'] for a in activities):
                continue
            params=json.loads(item['expected_parameter_record'])
            modes={p.get('mode') for p in params}
            desired='coal_sulfur_balance' if label['detail_cause']=='SULFUR_MISSING' else 'coal_particle_balance'
            assert desired in modes,(sid,item['pollutant'],modes)
            parameter_sources.add(sid)
            evidence.append(dict(source_id=sid,pollutant=item['pollutant'],field=label['field'],
                factor_mode=desired,parameter_record=item['expected_parameter_record'],
                standard_reference=item['expected_standard_reference']))
assert len(parameter_sources)==60
adjusted=[]
for label in labels:
    if label['root_cause'] in ('ACTIVITY_MISSING','HARD_CONSTRAINT_CONFLICT') and 'NH3' in label['affected_pollutants'] and label['source_id'] not in slip_sources:
        label['affected_pollutants'].remove('NH3')
        adjusted.append(label['source_id'])
dump(HERE/'adjudication/objects.json',labels)
table(HERE/'adjudication/objects.csv',labels)
table(HERE/'adjudication/parameter_dependencies.csv',evidence)
print(json.dumps(dict(parameter_objects=len(parameter_sources),parameter_dependencies=len(evidence),nh3_label_corrections=adjusted),ensure_ascii=False))
