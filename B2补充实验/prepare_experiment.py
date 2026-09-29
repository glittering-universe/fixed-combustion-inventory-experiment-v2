from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from build_dataset import HERE, ROOT, PREPARED, workbook_values, dump, table

sys.path.insert(0, str(ROOT / 'experiment_control'))
import prepare_run


def validate():
    labels = json.loads((HERE / 'adjudication/objects.json').read_text())
    original = workbook_values(PREPARED / '基102-2002_原始结构脱敏输入.xlsx')
    actual = workbook_values(HERE / 'inputs/基102-2002_原始结构脱敏输入.xlsx')
    assert original[0] == actual[0]
    assert len({r['source_id'] for r in labels}) == 300
    audit=[]
    for label in labels:
        before = original[label['original_row']-1]
        after = actual[label['input_row']-1]
        changes = [original[0][i] for i,(a,b) in enumerate(zip(before,after)) if a!=b]
        expected = [label['field']] if label['field'] else []
        assert changes == expected, (label['source_id'],changes,expected)
        if expected:
            j=original[0].index(label['field'])
            assert before[j] == label['before'] and after[j] == label['after']
        audit.append(dict(source_id=label['source_id'],source_class=label['source_class'],
            root_cause=label['root_cause'],detail_cause=label['detail_cause'],
            changed_fields=changes,affected_pollutants=label['affected_pollutants'],
            expected_action='retain' if not expected else 'withhold_affected_pollutants',
            injection_valid=True))
    table(HERE/'adjudication/injection_review.csv',audit)
    print(json.dumps({'objects':len(audit),'injection_valid':sum(r['injection_valid'] for r in audit)},ensure_ascii=False))


def freeze():
    dest=HERE/'execution'
    dest.mkdir(exist_ok=False)
    for source, relative in ((ROOT/'plugin/fixed-combustion-inventory','plugin'),
                             (ROOT/'rules/frozen/v1.0.1','rules'),
                             (ROOT/'baseline/simple_deterministic','baseline'),
                             (ROOT/'method_package','method_package')):
        shutil.copytree(source,dest/relative,ignore=shutil.ignore_patterns('node_modules','__pycache__'))
    for file in ('score_b2.py','build_dataset.py','prepare_experiment.py','实验约束.txt'):
        if file != '实验约束.txt':
            shutil.copy2(HERE/file,dest/file)
    shutil.copy2(ROOT/'evaluation/normalize_run_output.py',dest/'normalize_run_output.py')
    for path in dest.rglob('*'):
        if path.is_file():
            path.chmod(0o444)
    for file in ('score_b2.py',):
        (HERE/file).chmod(0o444)
    print('execution package saved')


def prepare():
    prepare_run.RULE_PATH=HERE/'execution/rules'
    prepare_run.RULE_LOCK=prepare_run.RULE_PATH/'rule_package_lock.json'
    prepare_run.INPUT_ADAPTER=HERE/'execution/method_package/input_adapter.json'
    prepare_run.DETERMINISTIC_BASELINE=HERE/'execution/baseline'
    runs=[]
    for target,short in (('INDUSTRIAL','ind'),('POWER','pow')):
        for method,short_method,count in (('full','full',3),('generic_tool_agent','gen',3),('deterministic_program','det',1)):
            for repetition in range(1,count+1):
                run_id=f'B2-300-{short.upper()}-{short_method.upper()}-R{repetition}'
                profile=f'b2-300-{short}-{short_method}-r{repetition}' if method!='deterministic_program' else ''
                run_dir=HERE/'runs'/run_id
                args=argparse.Namespace(run_id=run_id,method=method,scenario='B2',target=target,scale='100',
                    variant='B2-300',export_mode='machine',output=run_dir,input_dir=HERE/'inputs')
                manifest=prepare_run.prepare_run_package(args)
                manifest['profile']=profile
                if method!='deterministic_program':
                    prompt='full_prompt.md' if method=='full' else 'generic_agent_prompt.md'
                    manifest['prompt_path']=str(HERE/'execution/method_package'/prompt)
                    completed=subprocess.run(['/Users/wushuo/.local/bin/hermes','profile','create',profile,
                        '--clone-from','fixed-combustion-inventory',
                        '--description',f'固定燃烧源 B2 300 {short.upper()} {short_method.upper()} R{repetition}'],
                        text=True,capture_output=True,check=True)
                    profile_path=Path('/Users/wushuo/.hermes/profiles')/profile
                    if method=='full':
                        shutil.copytree(HERE/'execution/plugin',profile_path/'plugins/fixed-combustion-inventory')
                dump(run_dir/'run_manifest.json',manifest)
                runs.append(dict(run_id=run_id,source_class=target,method=method,repetition=repetition,
                                 profile=profile,path=str(run_dir)))
    dump(HERE/'run_matrix.json',runs)
    print(json.dumps(runs,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('validate','freeze','prepare'))
    args=parser.parse_args()
    {'validate':validate,'freeze':freeze,'prepare':prepare}[args.action]()
