"""Prepare current paper tables and raw experiment release assets."""
import csv
import io
import json
import shutil
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
PAPER=ROOT/'论文写作材料'
RAW=ROOT/'原始实验材料'
B2=ROOT/'B2补充实验'
METHOD={'full':'Full完整方法','generic_tool_agent':'通用工具Agent','deterministic_program':'简单确定性脚本'}
TARGET={'INDUSTRIAL':'工业锅炉','POWER':'火电热力'}


def read_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def csv_text(rows):
    out=io.StringIO(newline='')
    writer=csv.DictWriter(out,fieldnames=list(rows[0]),lineterminator='\n')
    writer.writeheader();writer.writerows(rows)
    return '\ufeff'+out.getvalue()


def copy(source,destination):
    destination.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,destination)


def chat_text(run_id,session):
    result=[f'# {run_id} Hermes聊天记录','',f"会话：{session['id']}",f"模型：{session['model']}",'']
    if session.get('system_prompt'):result+=['## 系统提示','',session['system_prompt'],'']
    for n,msg in enumerate(session['messages'],1):
        result += [f"## {n}. {msg['role']}",'',msg.get('content') or '', '']
        if msg.get('tool_calls'):result+=['```json',json.dumps(msg['tool_calls'],ensure_ascii=False,indent=2),'```','']
    return '\n'.join(result)


def main():
    for source,name in ((B2/'B2补充实验结果.md','结果报告.md'),(B2/'results/per_run.csv','逐次指标.csv'),
        (B2/'results/summary.csv','汇总指标.csv'),(B2/'results/分类别结果.csv','分类别结果.csv'),
        (B2/'results/处置问题明细.csv','处置问题明细.csv')):
        copy(source,PAPER/'03_实验结果/B2补充实验'/name)
    for source,name in ((B2/'adjudication/objects.csv','B2补充对象与注入.csv'),
        (B2/'adjudication/injection_review.csv','B2补充注入核验.csv'),
        (B2/'adjudication/parameter_dependencies.csv','B2补充参数依赖.csv'),
        (B2/'diagnostics/既有异常诊断.csv','B2既有对象诊断.csv')):
        copy(source,PAPER/'04_核算依据'/name)

    scores=read_csv(PAPER/'03_实验结果/全部运行记录.csv')
    for experiment in 'ABCD':
        archive=RAW/f'experiment-{experiment}.zip'
        replacement=RAW/f'experiment-{experiment}.current.zip'
        with ZipFile(archive) as old,ZipFile(replacement,'w',ZIP_DEFLATED,compresslevel=5) as new:
            for entry in old.infolist():
                if entry.filename==f'实验{experiment}/逐次评分.csv':
                    content=csv_text([r for r in scores if r['experiment']==experiment]).encode('utf-8')
                elif entry.filename==f'实验{experiment}/README.md':
                    content=(PAPER/'06_原始实验材料/README.md').read_bytes()
                else:
                    content=old.read(entry)
                new.writestr(entry,content)
        replacement.replace(archive)

    index_path=PAPER/'06_原始实验材料/运行与聊天索引.csv'
    index=[r for r in read_csv(index_path) if not r['运行编号'].startswith('B2-300-')]
    extra=[]
    with ZipFile(RAW/'experiment-B2-supplement.zip','w',ZIP_DEFLATED,compresslevel=5) as archive:
        for run in json.loads((B2/'run_matrix.json').read_text()):
            run_id=run['run_id'];base=Path(run['path']);prefix=f'实验B2补充/{run_id}'
            row=dict(实验='B2补充',运行编号=run_id,方法=METHOD[run['method']],源类=TARGET[run['source_class']],
                结果目录=f'{run_id}/结果',Hermes会话='',消息数='',工具调用数='',聊天记录='',材料类型='机器运行输出')
            for folder,name in (('outputs','结果'),('trace','计算过程')):
                for path in sorted((base/folder).rglob('*')):
                    if path.is_file() and not path.name.endswith('.inspect.ndjson'):
                        archive.write(path,f'{prefix}/{name}/{path.relative_to(base/folder)}')
            for name in ('execution_metrics.json','hermes_usage.json'):
                path=base/'logs'/name
                if path.exists():archive.write(path,f'{prefix}/运行记录/{name}')
            archive.write(B2/'results'/run_id/'objects.csv',f'{prefix}/逐对象评价.csv')
            if run['profile']:
                path=base/'session_export/session.jsonl'
                session=json.loads(path.read_text(encoding='utf-8'))
                archive.write(path,f'{prefix}/聊天记录/session.jsonl')
                archive.writestr(f'{prefix}/聊天记录/聊天记录.md',chat_text(run_id,session))
                row.update(Hermes会话=session['id'],消息数=len(session['messages']),工具调用数=session['tool_call_count'],聊天记录=f'{run_id}/聊天记录/聊天记录.md')
            extra.append(row)
        archive.writestr('实验B2补充/运行索引.csv',csv_text(extra))
        archive.write(B2/'results/per_run.csv','实验B2补充/逐次评分.csv')
        archive.write(B2/'B2补充实验结果.md','实验B2补充/结果报告.md')
    index_path.write_text(csv_text(index+extra),encoding='utf-8')
    copy(index_path,RAW/'run-chat-index.csv')
    copy(PAPER/'06_原始实验材料/README.md',RAW/'README.md')
    print(json.dumps({'indexed_runs':len(index+extra),'supplement_runs':len(extra),'supplement_sessions':sum(bool(r['Hermes会话']) for r in extra)},ensure_ascii=False))


if __name__=='__main__':main()
