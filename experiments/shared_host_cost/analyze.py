"""Cost evidence, not packet-CPU attribution or a wall-time speedup claim."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from moe_ub_cosim.host_timing import read_trace
from experiments.shared_uplink_choice.prepare import load, save, sha


def need(ok,message):
    if not ok:raise ValueError(message)


def normalized(result, identity):
    value=deepcopy(result)
    files=identity['files'];libraries=[]
    for name in value['loaded_libraries']:
        base=Path(name).name
        if base.startswith('libns3.'):
            need('lib/'+base in files,'unexpected native library')
            # The historical and current path may differ; both must contain the accepted bytes.
            need(Path(name).is_file() and sha(Path(name))==files['lib/'+base],'unaccepted loaded library')
            libraries.append(base)
        else:libraries.append(name)
    value['loaded_libraries']=libraries
    return value


def equal_result(old,new,identity):
    need(normalized(old,identity)==normalized(new,identity),'observed timing changed complete native behavior')


def equal_snapshot(old,new):need(old==new,'application/generic execution changed')


def compare(stage,batch,mode):
    reference=stage/'reference'/f'batch{batch}'/mode
    directory=stage/'truth'/f'batch{batch}'/mode
    identity=load(stage/'runtime-identity.json')
    old,new=load(reference/'result.json'),load(directory/'result.json')
    equal_result(old,new,identity)
    snapshot='modern-execution.json' if mode=='modern' else 'event-execution.json'
    equal_snapshot(load(reference/snapshot),load(directory/snapshot))
    need((reference/'control.jsonl').read_bytes()==(directory/'control.jsonl').read_bytes(),'control contents/order changed')
    need(new['passed'] and new['packet_capture']['omitted_events']==0 and new['session']['finished'],'incomplete network')
    traces={name:read_trace(directory/(name+'_host.jsonl')) for name in ('worker','native')}
    need(all(t['trace_complete'] for t in traces.values()),'incomplete host traces')
    return dict(passed=True,complete_result_equivalent=True,execution_snapshot_equivalent=True,control_bytes_equivalent=True,
                timing_records={n:t['records'] for n,t in traces.items()})


def independent(rows):
    """Reconstruct intervals after parsing; does not use the public parser's stack."""
    need(rows[0]['type']=='header' and rows[-1]['type']=='footer' and rows[-1]['complete'],'missing complete trace boundaries')
    starts,ends={},{}
    last_wall=last_cpu=0
    for row in rows[1:-1]:
        need(row['wall_ns']>=last_wall and row['cpu_ns']>=last_cpu,'nonmonotone time')
        last_wall,last_cpu=row['wall_ns'],row['cpu_ns']
        if row['type']=='begin':
            need(row['id'] not in starts,'duplicate interval')
            starts[row['id']]=row
        elif row['type']=='end':
            need(row['id'] not in ends,'duplicate end')
            ends[row['id']]=row
        else:need(row['type']=='snapshot','unexpected record')
    need(starts.keys()==ends.keys() and len(starts)==rows[-1]['spans'],'unclosed interval')
    children=defaultdict(list)
    for identity,start in starts.items():
        end=ends[identity]
        need(start['wall_ns']<=end['wall_ns'] and start['cpu_ns']<=end['cpu_ns'],'negative interval')
        parent=start['parent']
        if parent:
            need(parent in starts and starts[parent]['wall_ns']<=start['wall_ns'] and end['wall_ns']<=ends[parent]['wall_ns'],
                 'interval outside declared parent')
        children[parent].append(identity)
    for group in children.values():
        group.sort(key=lambda i:starts[i]['wall_ns'])
        need(all(ends[a]['wall_ns']<=starts[b]['wall_ns'] for a,b in zip(group,group[1:])),'overlapping sibling intervals')
    totals={}
    for identity,start in starts.items():
        end=ends[identity]
        phase=totals.setdefault(start['name'],dict(count=0,inclusive_wall_ns=0,inclusive_cpu_ns=0,exclusive_wall_ns=0,exclusive_cpu_ns=0))
        phase['count']+=1
        for clock in ('wall','cpu'):
            delta=end[clock+'_ns']-start[clock+'_ns']
            nested=sum(ends[c][clock+'_ns']-starts[c][clock+'_ns'] for c in children[identity])
            need(delta>=nested,'child clocks exceed parent')
            phase['inclusive_'+clock+'_ns']+=delta
            phase['exclusive_'+clock+'_ns']+=delta-nested
    return totals


def metrics(directory,mode):
    traces={name:read_trace(directory/(name+'_host.jsonl')) for name in ('worker','native')}
    for name,trace in traces.items():
        rows=[__import__('json').loads(line) for line in (directory/(name+'_host.jsonl')).read_text().splitlines()]
        need(trace['trace_complete'] and independent(rows)==trace['phases'],'independent host phase totals disagree')
    worker,native=traces['worker']['phases'],traces['native']['phases']
    root=worker['modern_worker' if mode=='modern' else 'generic_worker']
    total=native['native_total']
    need(root['count']==total['count']==1,'ambiguous root CPU')
    cpu=root['inclusive_cpu_ns']+total['inclusive_cpu_ns']
    fraction=total['inclusive_cpu_ns']/cpu
    snapshots=[s for s in traces['native']['snapshots'] if 'processed_events' in s['values']]
    last=max(snapshots,key=lambda s:(s['wall_ns'],s['values']['processed_events']))
    result=load(directory/'result.json')
    return dict(worker_root=root,native_root=total,worker_phases=worker,native_phases=native,
        observed_CPU_ns=cpu,all_native_CPU_fraction=fraction,zero_native_CPU_work_factor=1/(1-fraction),
        run_exclusive_CPU_fraction=native['simulator_run']['exclusive_cpu_ns']/cpu,
        pure_packet_CPU_measured=False,wall_speedup_bound=False,
        last_native_snapshot=last['values'],last_snapshot_name=last['name'],
        source_packets=len(result['transactions']['transport']['source_packets']),
        physical_TX=sum(r[3]==0 for r in result['packet_capture']['events']),
        trace_records={n:t['records'] for n,t in traces.items()})


def report(stage,output):
    output.mkdir()
    ledger=load(stage/'execution.json')
    rows={}
    for batch in (4,8):
        for mode in ('modern','generic'):
            directory=stage/'truth'/f'batch{batch}'/mode
            if not (directory/'equivalence.json').exists():continue
            need(load(directory/'equivalence.json')['passed'],'timing behavior not accepted')
            rows[f'batch{batch}-{mode}']=metrics(directory,mode)
    save(output/'summary.json',dict(execution=ledger,measurements=rows,hardware_calibrated=False,
        pure_packet_CPU_measured=False,wall_speedup_measured=False,novel_simulator_speedup_proven=False))
    lines=['# 共享上联负载的宿主成本与加速空间','',
        '保持已验收网络不变，仅启用原有旁路宿主计时。CPU是模拟器在宿主机上的工作量，不是被仿真的GPU/网络时间。每输入/执行器只观测一次，不作为稳定速度排名。','',
        '| 输入/执行器 | Python CPU s | native CPU s | native CPU占比 | Run exclusive CPU s | 令全部native免费后的CPU工作量倍数 |','|---|---:|---:|---:|---:|---:|']
    for name,row in rows.items():
        lines.append(f"| {name} | {row['worker_root']['inclusive_cpu_ns']/1e9:.6f} | {row['native_root']['inclusive_cpu_ns']/1e9:.6f} | {100*row['all_native_CPU_fraction']:.2f}% | {row['native_phases']['simulator_run']['exclusive_cpu_ns']/1e9:.6f} | {row['zero_native_CPU_work_factor']:.3f} |")
    lines+=['','CPU上限的前提是Python工作保持不变、把初始化/报告/会话/网络等全部native CPU都当可消除；比仅优化包处理更加宽松。分母省略外层CLI CPU，不能用作墙钟、千卡或推理加速上限。Run exclusive仍混合网络、传输、内存与事件分派，并非纯包CPU。','',
        '| 输入/执行器 | 最后快照已处理ns-3事件 | 快照仿真时钟 ns | 源数据包 | 全部物理发送 | worker/native计时记录 |','|---|---:|---:|---:|---:|---|']
    for name,row in rows.items():
        snap=row['last_native_snapshot'];records=row['trace_records']
        lines.append(f"| {name} | {snap['processed_events']} | {snap['now_ns']} | {row['source_packets']} | {row['physical_TX']} | {records['worker']}/{records['native']} |")
    lines+=['','最后快照只证明该观测点的事件计数；ns-3事件含非包事件，不等于数据包。完整图是普通冻结图基线，原网络编译成本与真实现代库校准仍未包含。', '',
        f"新增观测网络{sum(r['network'] for r in ledger['records'])}次；阶段状态`{ledger['state']}`。错误与未执行条件保留，不增加预算或补跑。",'',
        '是否值得实施包级加速，应结合这些宿主成本、较强通用图基线和跨多跳/反馈的安全条件判断；本轮尚未实现新算法或证明论文创新。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('compare','report'))
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path)
    p.add_argument('--batch',type=int,choices=(4,8));p.add_argument('--mode',choices=('modern','generic'))
    a=p.parse_args()
    if a.action=='report':report(a.stage.resolve(),a.output.resolve());return
    value=compare(a.stage.resolve(),a.batch,a.mode)
    save(a.stage/'truth'/f'batch{a.batch}'/a.mode/'equivalence.json',value)


if __name__=='__main__':main()
