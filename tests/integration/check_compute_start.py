"""Independent start/queue/contribution evidence from saved runs; no network runs."""
import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
from html import escape
import json
from pathlib import Path


def load(path):return json.loads(path.read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):
    with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
def frames(path):
    return [json.loads(line.removeprefix('MOE_RDMA_EVENT ')) for line in path.read_text().splitlines()
            if line.startswith('MOE_RDMA_EVENT ')]


def harness(path):
    raw=frames(path);report=raw[-1]
    assert report['type']=='COMPUTE_CHECK' and report['passed'] and report['queue']['empty']
    expected={1:(0,0,10),2:(0,0,3),3:(2,3,7),4:(1,10,12),5:(1,12,12),6:(10,12,17)}
    observed=defaultdict(dict)
    for i,e in enumerate(raw[:-1]):
        assert e['sequence']==i and e['schema_version']=='moe-execution-event/2'
        if 'node' in e:
            assert e['kind'] not in observed[e['node']]
            observed[e['node']][e['kind']]=e['time_ns']
    for t in report['queue']['tasks']:
        exp=expected[t['node']]
        assert tuple(t[k] for k in ('ready_ns','start_ns','done_ns'))==exp
        assert tuple(observed[t['node']][k] for k in ('node_issue','node_compute_start','node_complete'))==exp
    assert len(observed)==6 and len(raw)==21 and report['events']['events']==20
    return dict(passed=True,tasks=6,events=20,max_queue_wait_ns=11,source_sha256=sha(path))


def inspect(run):
    c=load(run/'input.json');r=load(run/'result.json');f=load(run/'frontend.json')
    assert c['execution_events']['schema_version']=='moe-execution-events/2'
    assert r['passed'] and r['drained'] and r['gpu_count']==128
    assert r['service']['shared_compute']['empty']
    assert r['packet_capture']['complete'] and not r['packet_capture']['omitted_events']
    waves={h['graph_id']:load(run/'graphs'/h['graph_id']/'wave.json') for h in f['history']}
    nodes={(g,int(rank),n['id']):n for g,w in waves.items() for rank,rows in w['rank_nodes'].items() for n in rows}
    raw=frames(run/'native.stdout.log');events=[e for e in raw if e['type']=='OBSERVE']
    recorded=[json.loads(line) for line in (run/'execution_events.jsonl').read_text().splitlines()]
    assert recorded==events and len(events)==r['service']['execution_events']['events']
    by_node=defaultdict(dict)
    for i,e in enumerate(events):
        assert e['sequence']==i and e['schema_version']=='moe-execution-event/2'
        assert i==0 or e['time_ns']>=events[i-1]['time_ns']
        if e['kind'].startswith('node_'):
            key=e['graph_id'],e['rank'],e['node']
            assert key in nodes and e['kind'] not in by_node[key]
            by_node[key][e['kind']]=e['time_ns']
    originals={};queues=defaultdict(list);waits=[]
    for t in r['service']['shared_compute']['tasks']:
        assert t['done_ns']-t['start_ns']==t['duration_ns']
        queues[t['rank']].append(t)
        if t['kind']=='original':
            key=t['graph_id'],t['rank'],t['node'];n=nodes[key];e=by_node[key]
            assert key not in originals and n['kind']=='COMP' and t['duration_ns']==n['duration_ns']
            assert tuple(t[k] for k in ('ready_ns','start_ns','done_ns'))==tuple(e[k] for k in ('node_issue','node_compute_start','node_complete'))
            originals[key]=t
        if t['start_ns']>t['ready_ns']:waits.append(dict(t,wait_ns=t['start_ns']-t['ready_ns']))
    assert set(originals)=={key for key,n in nodes.items() if n['kind']=='COMP'}
    for key,n in nodes.items():
        assert ('node_compute_start' in by_node[key])==(n['kind']=='COMP')
        assert all(by_node[key]['node_issue']>=by_node[key[0],key[1],dep]['node_complete'] for dep in n['deps'])
    for rows in queues.values():
        end=0
        for t in sorted(rows,key=lambda t:t['sequence']):
            assert t['start_ns']==max(end,t['ready_ns']);end=t['done_ns']
    bindings=[json.loads(line) for line in (run/'contribution_calls.jsonl').read_text().splitlines()]
    for call in bindings:
        for b in call['binding']['bundles']:
            task=originals[call['binding']['graph_id'],b['rank'],b['expert_node']]
            attempt=call['state']['attempts']['original.'+b['id']]
            assert attempt['compute_begin_ns']==task['start_ns'] and attempt['compute_done_ns']==task['done_ns']
    return dict(passed=True,application_finished_ns=max(w['finished_ns'] for w in r['service']['waves']),
        network_drained_ns=r['finish_ns'],original_compute_tasks=len(originals),execution_events=len(events),
        original_queued_tasks=sum(t['kind']=='original' for t in waits),queued_tasks=waits,
        helper_events=[e for e in raw if e['type']=='HELPER_OBSERVE'],
        original_messages=sum(len(w['messages']) for w in waves.values()),
        policy=c['inflight_helpers']['policy'],
        source_sha256={str(p.relative_to(run)):sha(p) for p in [run/'input.json',run/'rdma.json',run/'topology.json',run/'result.json',run/'execution_events.jsonl',run/'contribution_calls.jsonl']}),c,waves


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--harness',type=Path,required=True)
    p.add_argument('--run',type=Path,action='append',default=[]);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    report=dict(passed=True,harness=harness(a.harness),conditions={},scope='functional FIFO/start evidence; synthetic routes and costs; no recovery-benefit claim')
    configs=[];graphs=[]
    for run in a.run:
        row,c,w=inspect(run);report['conditions'][run.name]=row
        configs.append(deepcopy(c));configs[-1]['inflight_helpers']['policy']='same'
        configs[-1]['route_replay']['manifest']['path']='same'
        graphs.append(w)
    if a.run:
        assert len(a.run)==2 and configs[0]==configs[1] and graphs[0]==graphs[1]
        first,second=report['conditions'].values()
        assert first['policy']=='observe_only/1' and second['policy']=='first_missing_bundle/1'
        assert all(first['source_sha256'][name]==second['source_sha256'][name] for name in ('rdma.json','topology.json'))
        assert any(row['original_queued_tasks']>0 for row in report['conditions'].values()), 'stress case did not produce original GPU queueing'
        report['application_change_ns']=second['application_finished_ns']-first['application_finished_ns']
        report['application_change_percent']=100*report['application_change_ns']/first['application_finished_ns']
    save(out/'verification.json',report)
    cards=[]
    for name,row in report['conditions'].items():
        cards.append(f'<h2>{escape(name)}</h2><p>任务完成 {row["application_finished_ns"]/1000:.3f} µs；原计算任务 {row["original_compute_tasks"]}；发生排队的原任务 {row["original_queued_tasks"]}。</p>')
        cards.append('<table><tr><th>任务</th><th>GPU</th><th>提交 µs</th><th>开始 µs</th><th>完成 µs</th><th>排队 µs</th></tr>')
        for t in row['queued_tasks']:
            label=t.get('graph_id','helper')+'.'+str(t.get('node',t.get('kind')))
            values=[escape(label),str(t['rank']),*[f'{t[k]/1000:.3f}' for k in ('ready_ns','start_ns','done_ns','wait_ns')]]
            cards.append('<tr>'+''.join('<td>'+v+'</td>' for v in values)+'</tr>')
        cards.append('</table>')
    comparison=(f'<p><strong>本压力用例中，备用执行增加任务耗时 {report["application_change_ns"]/1000:.3f} µs（{report["application_change_percent"]:.2f}%）。</strong>这说明本例简单备用策略存在资源干扰，不能作为新算法收益；成熟负载感知备用方法仍需比较。</p>' if a.run else '')
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>共享GPU实际开始与排队核验</title><style>body{font:17px/1.7 system-ui;margin:36px auto;padding:0 24px;max-width:1100px;color:#182b40;background:#f6f8fc}table{border-collapse:collapse;background:white;width:100%;font-size:15px}th,td{border:1px solid #cad4e0;padding:10px;text-align:left}h1{font-size:28px}h2{margin-top:30px}</style><h1>提交计算，不等于已经开始计算</h1><p>新记录区分任务提交、实际占用GPU引擎、实际完成。排队期间网络接收与ACK继续运行，阶段门限保持。128端点Clos、两个EP64副本、四个人工请求，两层两步；两组都有GPU6前20µs的数据故障，区别只有备用策略。</p><p>这是人工计算压力下的功能核验，专家单位成本50µs不是GPU实测。不能将队列记录功能、故障增加的耗时或单贡献提前量写成创新收益。</p>'''+comparison+''.join(cards)+'<p>详细记录见 <a href="verification.json">verification.json</a>。全局研究目标仍在进行，当前接口保留原调用完成依赖。</p></html>'
    (out/'report.html').write_text(html)
    print(json.dumps(dict(passed=True,conditions=len(a.run),harness_tasks=6)))


if __name__=='__main__':main()
