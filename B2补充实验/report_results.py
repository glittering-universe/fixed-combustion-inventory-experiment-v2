import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

HERE=Path(__file__).resolve().parent
TARGET={'INDUSTRIAL':'工业锅炉','POWER':'火电热力'}
METHOD={'full':'Full','generic_tool_agent':'通用Agent','deterministic_program':'确定性脚本'}
CAUSE={'ACTIVITY_MISSING':'活动水平缺失','POLLUTANT_PARAMETER_MISSING':'污染物参数缺失',
       'SOURCE_RELATION_MISSING':'源关系缺失','HARD_CONSTRAINT_CONFLICT':'硬约束冲突','NO_ANOMALY':'正常对照'}


def read(path):
    with path.open(encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))


def write(path,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def pct(value):
    return f'{100*float(value):.1f}%'


def ci(row):
    return f"{pct(row['estimate'])} [{pct(row['ci95_lower'])}, {pct(row['ci95_upper'])}]"


def main():
    data=read(HERE/'results/per_run.csv')
    matrix=json.loads((HERE/'run_matrix.json').read_text())
    labels=json.loads((HERE/'adjudication/objects.json').read_text())
    by_run=defaultdict(dict)
    for r in data:by_run[r['run']][r['metric']]=r
    lines=['# B2补充实验结果','',f'已完成 {len(by_run)}/{len(matrix)} 次运行。','',
           '## 样本','', '| 源类 | 活动水平缺失 | 污染物参数缺失 | 源关系缺失 | 硬约束冲突 | 正常对照 | 合计 |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for target in TARGET:
        c=Counter(r['root_cause'] for r in labels if r['source_class']==target)
        lines.append('| '+TARGET[target]+' | '+' | '.join(str(c[k]) for k in CAUSE)+' | '+str(sum(c.values()))+' |')
    lines+=['','## 运行结果','', '| 源类 | 方法 | 重复 | 四类宏F1 | 根因召回率 [95% CI] | 定向处置成功率 [95% CI] | 正常对照误报率 [95% CI] | 输出缺失对象数 |',
            '|---|---|---:|---:|---|---|---|---:|']
    case_rows=[]
    issue_rows=[]
    index=[]
    for run in matrix:
        rid=run['run_id']
        if rid not in by_run:continue
        metrics=by_run[rid]
        lines.append('| '+ ' | '.join([TARGET[run['source_class']],METHOD[run['method']],str(run['repetition']),
            pct(metrics['macro_f1']['estimate']),ci(metrics['root_recall']),ci(metrics['targeted_success_rate']),
            ci(metrics['false_alarm_rate']),metrics['macro_f1']['missing_source_outputs']])+' |')
        objects=read(HERE/'results'/rid/'objects.csv')
        for root in CAUSE:
            group=[r for r in objects if r['expected_root']==root]
            case_rows.append(dict(源类=TARGET[run['source_class']],方法=METHOD[run['method']],重复=run['repetition'],
                对象类别=CAUSE[root],对象数=len(group),根因正确数=sum(int(r['root_recalled']) for r in group),
                定向处置成功数=sum(int(r['targeted_success']) for r in group),
                标记异常对象数=sum(int(r['false_alarm']) for r in group),
                输出缺失对象数=sum(not int(r['complete_output']) for r in group)))
        for r in objects:
            if (r['expected_root']!='NO_ANOMALY' and not int(r['targeted_success'])) or (r['expected_root']=='NO_ANOMALY' and not int(r['normal_disposition_success'])):
                issue_rows.append(dict(运行=rid,源ID=r['source_id'],对象类别=CAUSE[r['expected_root']],
                    预测原因码=r['predicted_roots'],根因正确=r['root_recalled'],
                    定向处置成功=r['targeted_success'],缺失污染物=r['missing_pollutants']))
        path=Path(run['path'])
        metrics_raw=json.loads((path/'logs/execution_metrics.json').read_text())
        index.append(dict(运行=rid,源类=TARGET[run['source_class']],方法=METHOD[run['method']],重复=run['repetition'],
            Profile=run['profile'],会话ID=metrics_raw.get('session_id',''),
            结果目录=str((path/'outputs').relative_to(HERE)),
            聊天记录=str((path/'session_export/session.jsonl').relative_to(HERE)) if run['profile'] else '',
            耗时秒=metrics_raw['wall_seconds']))
    lines+=['','## 分类别结果','', '| 源类 | 方法 | 重复 | 对象类别 | 根因正确数 | 定向处置成功数 | 对象数 |',
            '|---|---|---:|---|---:|---:|---:|']
    for r in case_rows:
        if r['对象类别']=='正常对照':continue
        lines.append('| '+' | '.join(str(r[k]) for k in ('源类','方法','重复','对象类别','根因正确数','定向处置成功数','对象数'))+' |')
    lines+=['','## 原4个对象的诊断','', '| 源ID | 基102行号 | 源类 | 燃料 | 已有参照中的待核原因 |',
            '|---|---:|---|---|---|']
    for r in read(HERE/'diagnostics/既有异常诊断.csv'):
        reasons=json.loads(r['既有待核原因'])
        lines.append('| '+' | '.join((r['源ID'],r['基102行号'],TARGET[r['源类']],r['燃料一'],', '.join(reasons) if reasons else '无待核原因'))+' |')
    write(HERE/'results/分类别结果.csv',case_rows)
    write(HERE/'results/运行与会话.csv',index)
    if issue_rows:write(HERE/'results/处置问题明细.csv',issue_rows)
    (HERE/'B2补充实验结果.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'completed_runs':len(by_run),'report':str(HERE/'B2补充实验结果.md')},ensure_ascii=False))


if __name__=='__main__':main()
