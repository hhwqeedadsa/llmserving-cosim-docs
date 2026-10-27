"""Cost evidence, not packet-CPU attribution or a wall-time speedup claim."""
import argparse
import html
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
    compiler=load(stage/'compiler-evidence/summary.json')
    save(output/'summary.json',dict(execution=ledger,measurements=rows,hardware_calibrated=False,
        compiler=compiler,optimized_backend_performance_measured=False,
        pure_packet_CPU_measured=False,wall_speedup_measured=False,novel_simulator_speedup_proven=False))
    lines=['# 共享上联负载的宿主成本与加速空间','',
        '保持已验收网络不变，仅启用原有旁路宿主计时。CPU是模拟器在宿主机上的工作量，不是被仿真的GPU/网络时间。每输入/执行器只观测一次，不作为稳定速度排名。','',
        '**重要限制：现有构建虽标记Release，437个编译单元均以`-O3 ... -O0`结尾，实际为`-O0`。固定原生ns-3的NS3_WARNINGS=ON分支在告警选项中追加-O0，覆盖-O3。本轮没有构建或测量优化版本，不能把当前CPU占比当作优化后基线，也不能用消除该编译设置宣称算法创新。**','',
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
        '原生CPU的主要exclusive阶段（秒）：','']
    for name,row in rows.items():
        phases=sorted(row['native_phases'].items(),key=lambda pair:-pair[1]['exclusive_cpu_ns'])[:6]
        lines.append(name+'：'+ '；'.join(f"{n}={p['exclusive_cpu_ns']/1e9:.6f}" for n,p in phases)+'。')
    lines+=['','原生占比80%—86%不等于包处理占比。Simulator::Run exclusive仅占已观测总CPU约9%—10%，仍混合传输、内存和事件分派；会话推进、排空检查和候选校验占用较多。原有增量progress已经启用，全量对比仅在未启用的开发宏MOE_VERIFY_PROGRESS_UPDATES内，不能把那段源码误认为运行中的全量扫描。', '',
        '下一步先独立修复Release构建并建立优化后的强基线，再判断是否投入安全聚合或会话调度优化。原有四次额度已用尽，本轮不编译、不追加网络。是否值得实施包级加速，还需优化后成本与跨多跳/反馈的安全条件；本轮尚未实现新算法或证明论文创新。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    table=[]
    for name,row in rows.items():
        table.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in (name,
            f"{row['worker_root']['inclusive_cpu_ns']/1e9:.3f}",
            f"{row['native_root']['inclusive_cpu_ns']/1e9:.3f}",
            f"{row['all_native_CPU_fraction']*100:.2f}%",
            f"{row['run_exclusive_CPU_fraction']*100:.2f}%"))+'</tr>')
    (output/'report.html').write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>仿真器的时间花在哪里</title>
<style>body{font:16px/1.8 system-ui,sans-serif;background:#f2f6fa;color:#183047;margin:0}main{max-width:1000px;margin:auto;padding:32px 22px 60px}section{background:white;border:1px solid #d9e3eb;border-radius:12px;padding:22px;margin:22px 0}.warning{border-left:5px solid #b45b22;background:#fff1df;padding:18px}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{text-align:left;padding:10px;border-bottom:1px solid #dfe6ed}.scroll{overflow:auto}a{color:#12679d}</style>
<main><p>MoE Scale-Out · 宿主成本核查 · 2026-10-27</p><h1>仿真器的时间花在哪里</h1>
<p>四次计时执行保持原有网络结果。当前证据要求先建立优化后的强基线，再判断包级加速的价值。</p>
<p class="warning">发现构建设置问题：虽然标记Release，但437个编译单元实际都以-O0编译。原生ns-3把-O0与告警选项一起加入，覆盖了-O3。下表不能代表优化后的后端性能。</p>
<section><h2>当前构建的观测</h2><div class="scroll"><table><thead><tr><th>输入/执行器</th><th>Python CPU 秒</th><th>原生 CPU 秒</th><th>原生占比</th><th>Run exclusive占总CPU</th></tr></thead><tbody>'''+''.join(table)+'''</tbody></table></div>
<p>CPU是宿主机运行模拟器的工作量。每条件只观测一次，包含计时记录开销；现代控制器与通用图不作稳定速度排名。</p></section>
<section><h2>为什么不能直接说包级优化收益很大</h2><p>原生进程占比80%—86%，其中包含会话推进、完成状态扫描、候选校验、初始化和报告。Run exclusive约占总CPU的9%—10%，仍包含网络、传输、内存和事件分派，不能视为纯包处理。</p>
<p>把全部原生CPU免费只能得到一个极宽松的CPU工作量上限，不能换算墙钟、千卡或GPU推理加速。跨进程重叠的墙钟也不能相加。</p></section>
<section><h2>下一步怎样推进</h2><p>先独立修复构建并核对每个实际编译单元，建立优化后的强基线；再测量哪些处理仍然主导成本。普通编译优化、已有回调批处理与增量扫描不算论文创新。</p>
<p>研究候选仍是可靠的精简表示，以及保留多跳排队、反馈和应用完成的安全处理缩减；本轮没有证明新算法加速。场景只有四活动rank、单层真实路由通信调用，未完成现代硬件校准或千卡验证。</p>
<p><a href="report.md">完整说明</a> · <a href="summary.json">原始摘要</a> · <a href="../verification.json">独立核验</a></p></section></main></html>''')


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('compare','report'))
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path)
    p.add_argument('--batch',type=int,choices=(4,8));p.add_argument('--mode',choices=('modern','generic'))
    a=p.parse_args()
    if a.action=='report':report(a.stage.resolve(),a.output.resolve());return
    value=compare(a.stage.resolve(),a.batch,a.mode)
    save(a.stage/'truth'/f'batch{a.batch}'/a.mode/'equivalence.json',value)


if __name__=='__main__':main()
