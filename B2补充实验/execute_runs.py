import argparse
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent


def execute(row):
    run=Path(row['path'])
    if row['method']=='deterministic_program':
        command=[str(ROOT/'.venv/bin/python'),str(ROOT/'experiment_control/run_stage_sequence.py'),str(run)]
    else:
        mode='full' if row['method']=='full' else 'generic'
        command=[str(ROOT/'.venv/bin/python'),str(ROOT/'experiment_control/run_hermes_session.py'),str(run),
                 '--mode',mode,'--profile',row['profile'],'--title',row['run_id']]
    with (run/'logs/launcher.txt').open('w',encoding='utf-8') as log:
        result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
    return dict(run_id=row['run_id'],return_code=result.returncode)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('run_ids',nargs='+')
    args=parser.parse_args()
    rows={r['run_id']:r for r in json.loads((HERE/'run_matrix.json').read_text())}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(execute,rows[run_id]) for run_id in args.run_ids]
        for future in as_completed(futures):
            print(json.dumps(future.result()),flush=True)
