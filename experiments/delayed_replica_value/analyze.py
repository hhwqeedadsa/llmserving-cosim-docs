"""Matched single/eager/delayed result and packet analysis; no execution."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
from html import escape
import json
from pathlib import Path


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def inspect(path,policy):
    eager=policy!="single";delayed=policy=="delayed"
    c=load(path/'input.json');r=load(path/'result.json');f=load(path/'frontend.json');v=load(path/'verification.json')
    assert r['passed'] and r['drained'] and r['gpu_count']==128 and r['service']['service_finished'] and v['passed']
    assert r['packet_capture']['complete'] and r['packet_capture']['omitted_events']==0
    assert c['workload']['decode_steps']==1 and len(c['parallel']['groups'])==2
    assert all(len(group)==64 for group in c['parallel']['groups'])
    if not eager:
        assert c['route_replay']['request_layout']=='sparse_active_groups/1'
        assert f['admitted_group_indices']==[0] and len(f['requests'])==4 and len(f['history'])==1
    else:assert len(f['requests'])==8 and len(f['history']) in ((1,2) if delayed else (2,))
    manifest_path=path/c['route_replay']['manifest']['path']
    assert sha(manifest_path)==c['route_replay']['manifest']['sha256']
    manifest=load(manifest_path);raw={}
    assert len(manifest['files'])==4
    for file in manifest['files']:
        source=path/file['file']['path'];assert sha(source)==file['file']['sha256']
        raw[file['file_id']]=load(source)
    assert len({file['file']['sha256'] for file in manifest['files']})==4
    observed=[json.loads(line.removeprefix('MOE_RDMA_EVENT ')) for line in (path/'native.stdout.log').read_text().splitlines()
              if line.startswith('MOE_RDMA_EVENT ') and json.loads(line.removeprefix('MOE_RDMA_EVENT '))['type']=='OBSERVE']
    assert observed==[json.loads(line) for line in (path/'execution_events.jsonl').read_text().splitlines()]
    retired={e['graph_id']:e for e in observed if e['kind']=='graph_retired'}
    assert set(retired)=={h['graph_id'] for h in f['history']}
    assert 'g0.w0' in retired and (eager or set(retired)=={'g0.w0'})
    if delayed:delayed_prefix(c,r,f,path)
    elif eager:assert set(retired)=={'g0.w0','g1.w0'}
    waves={graph:load(path/'graphs'/graph/'wave.json') for graph in retired}
    node_times={(e['graph_id'],e['rank'],e['node'],e['kind']):e['time_ns'] for e in observed if e['kind'].startswith('node_')}
    native={w['graph_id']:w for w in r['service']['waves']}
    messages={m['id']:m for m in r['service_transport']['messages']}
    mapping={(q['rank_id'],q['slot']):q for q in c['route_replay']['requests']}
    payload=Counter();compute=Counter();member_count=0
    hidden=c['model']['hidden_size']*2;topk=c['model']['top_k'];width=c['model']['experts']//64
    align=lambda v,k:(v+k-1)//k*k
    dispatch=align(hidden+align(topk*8+(1+topk)*4,32),64);combine=align(hidden+align(topk*8,32),64)
    for graph,wave in waves.items():
        group=wave['rank_ids'];group_index=wave['group_index'];expected=Counter()
        assert native[graph]['start_ns']==(c['whole_call_replication']['hedge_after_ns'] if delayed and group_index==1 else 0) and native[graph]['finished_ns']==retired[graph]['time_ns']
        for layer in wave['routes']:
            assert len(layer['tokens'])==4
            for t in layer['tokens']:
                source=t['rank'];original=source if group_index==0 else c['parallel']['groups'][0][group.index(source)]
                q=mapping[original,t['slot']];experts=raw[q['file_id']][q['first_decode_entry']][str(c['route_replay']['layer_ids'][layer['layer']])][0]
                assert experts==t['experts'] and t['source']['file_id']==q['file_id']
                for dst in {group[e//width] for e in experts}:
                    if dst!=source:
                        expected[layer['layer'],'dispatch',source,dst]+=dispatch
                        expected[layer['layer'],'combine',dst,source]+=combine
        actual={(m['layer'],m['phase'],m['src'],m['dst']):m['bytes'] for m in wave['messages']}
        assert actual==dict(expected) and len(actual)==len(wave['messages'])
        for m in wave['messages']:
            real=messages[m['id']];assert all(real[k]==m[k] for k in m)
            assert real['ready_ns']<=real['receive_ready_ns']<=real['ack_ns']<=retired[graph]['time_ns']
            payload[graph]+=m['bytes']
        for rank,nodes in wave['rank_nodes'].items():
            rank=int(rank)
            for n in nodes:
                start=node_times[graph,rank,n['id'],'node_issue'];end=node_times[graph,rank,n['id'],'node_complete']
                assert start<=end<=retired[graph]['time_ns']
                assert all(start>=node_times[graph,rank,d,'node_complete'] for d in n['deps'])
                if n['kind']=='COMP':
                    assert end-start==n['duration_ns'];compute[graph]+=n['duration_ns']
        for gate in (g for g in r['service']['phase_completion']['gates'] if g['graph_id']==graph):
            assert len(gate['members'])==64 and gate['released_ns']==max(m['arrived_ns'] for m in gate['members'])
            for member in gate['members']:
                member_count+=1
                assert member['arrived_ns']==node_times[graph,member['rank'],member['node'],'node_issue']
                assert gate['released_ns']==node_times[graph,member['rank'],member['node'],'node_complete']
    assert len(messages)==sum(len(w['messages']) for w in waves.values())
    transport=r['service_transport']['native'];streams={s['id']:s for s in transport['streams']}
    assert len(streams)==232 and transport['closed'] and transport['physical_drained']
    source_rows=Counter((p[0],p[2],p[4],p[1],p[5],p[6],p[7]) for p in transport['source_packets'])
    device_rows=Counter((p[0],p[1],p[2],p[5],p[7],p[11],p[4]) for p in r['packet_capture']['events'] if p[1]<128 and p[3]==0 and p[6]==17)
    assert source_rows==device_rows
    ranges={s:[] for s in streams}
    for p in transport['source_packets']:
        assert (p[2],p[3],p[4])==tuple(streams[p[1]][k] for k in ('src','dst','source_nic'))
        assert 0<=p[5]<p[5]+p[6]<=streams[p[1]]['published_bytes'];ranges[p[1]].append((p[5],p[5]+p[6]))
    accepted={s['stream']:s['receiver_prefix'] for s in transport['selective_repeat']['states']}
    for sid,s in streams.items():
        end=covered=0
        for begin,stop in sorted(ranges[sid]):covered+=max(0,stop-max(begin,end));end=max(end,stop)
        assert covered==s['published_bytes']==s['submitted_bytes']==s['snd_una']==accepted[sid]
    assert sum(payload.values())==sum(accepted.values())==v['receiver_accepted_bytes']
    if eager:
        report=load(path/'whole_call_report.json');assert report==f['whole_call_replication'] and report['all_attempts_retired']
        call=report['calls']['segment0'];winning=min(retired,key=lambda g:retired[g]['sequence'])
        assert call['attempts'][call['winner']]['graph_id']==winning and report['result_ready_ns']==retired[winning]['time_ns']
        ready=report['result_ready_ns']
    else:ready=retired['g0.w0']['time_ns']
    assert ready==v['application_finished_ns'] and r['finish_ns']==v['network_drained_ns']
    source_bytes=sum(p[6] for p in transport['source_packets']);assert source_bytes==v['source_payload_bytes']
    loss=r.get('data_loss',{})
    return dict(passed=True,simulated_endpoints=128,active_data_ranks=len({m[k] for m in messages.values() for k in ('src','dst')}),
        logical_requests=4,inventory_requests=len(f['requests']),execution_attempt_requests=sum(q['state']!='NOT_STARTED' for q in f['requests']),messages=len(messages),packet_events=len(r['packet_capture']['events']),
        result_ready_ns=ready,attempts_finished_ns=max(e['time_ns'] for e in retired.values()),network_drained_ns=r['finish_ns'],
        unique_payload_bytes=sum(payload.values()),source_payload_bytes=source_bytes,source_data_packets=len(transport['source_packets']),
        retransmit_payload_bytes=source_bytes-sum(payload.values()),declared_compute_work_ns=sum(compute.values()),
        standby_memory=f.get('standby_memory'),control_wakeup=r['service'].get('control_wakeup'),
        fault_drops=loss.get('dropped_packets',0),fault_window_arrivals=loss.get('window_packets',0),phase_member_arrivals=member_count,
        sha256={n:sha(path/n) for n in ('input.json','topology.json','rdma.json','result.json','frontend.json','verification.json')})



def delayed_prefix(c,r,f,path):
    """Independently reconstruct the single-call policy from actual wire frames."""
    deadline=c['whole_call_replication']['hedge_after_ns'];done=False;started=False;skipped=False;rows=iter(f['transcript'])
    pending=None;ticket=0;timer=[]
    def record(kind,now,active):
        timer.append(dict(sequence=len(timer),time_ns=now,kind=kind,deadline_ns=active[0],ticket=active[1]))
    for line in (path/'native.stdout.log').read_text().splitlines(keepends=True):
        if not line.startswith('MOE_RDMA_EVENT '):continue
        assert line.endswith('\n');e=json.loads(line[len('MOE_RDMA_EVENT '):])
        if e['type']=='OBSERVE':
            if e['kind']=='graph_retired' and e['graph_id']=='g0.w0':done=True
            continue
        assert e['type']=='DECIDE';row=next(rows);assert row['event']==e;reply=row['reply'];now=e['now_ns']
        if e['wakeup_ns'] is not None:
            assert pending and e['wakeup_ns']==now==pending[0]==deadline
            record('fired',now,pending);pending=None
        if now==0:groups=['g0.w0'];target=deadline
        elif done and not started:
            assert now<=deadline;skipped=True;groups=[];target=None
        elif not started:
            assert now==deadline and e['wakeup_ns']==deadline
            groups=['g1.w0'];started=True;target=None
        else:groups=[];target=None
        assert [Path(p).name for p in reply['submissions']]==groups and reply['wake_at_ns']==target
        if pending and target!=pending[0]:record('cancelled',now,pending);pending=None
        if target is not None and pending is None:
            pending=(target,ticket);ticket+=1;record('armed',now,pending)
    assert next(rows,None) is None and pending is None and skipped!=started
    assert r['service']['control_wakeup']['events']==timer
    call=f['whole_call_replication']['calls']['segment0'];replica=call['attempts']['replica']
    assert (replica['submitted_ns'] is not None)==started
    ids={pair['replica_request'] for pair in call['request_pairs']}
    expected_bytes=0
    for q in f['requests']:
        if q['id'] not in ids:continue
        expected_bytes+=((q['initial_context_tokens']+1+15)//16)*16*2*c['model']['layers']*c['model']['hidden_size']*c['model']['activation_bytes']
        if skipped:
            assert q['state']=='NOT_STARTED' and q['scheduler_state']=='WAITING' and q['intertoken_ns']==[]
            assert q['computed_tokens']==q['initial_context_tokens']-1 and q['reached_tokens']==q['initial_context_tokens']
            assert q['skipped_before_execution_ns']==call['attempts']['primary']['finished_ns']==replica['skipped_ns']
        else:assert q['state']=='OBSERVATION_ENDED' and q['scheduler_state']=='FINISHED' and len(q['intertoken_ns'])==1
    assert f['standby_memory']==dict(initial_reserved_bytes=expected_bytes,releases=[dict(group_index=1,
        time_ns=replica['finished_ns'] if started else replica['skipped_ns'],
        kind='executed_release' if started else 'skipped_release',bytes=expected_bytes)],final_used_bytes=0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('frozen','normal','local','output'):parser.add_argument('--'+name,type=Path,required=True)
    a=parser.parse_args();frozen=load(a.frozen);assert frozen['passed']
    assert sha(Path(__file__).with_name('protocol.md'))==frozen['method_sha256']
    assert sha(Path(frozen['backend']['path']))==frozen['backend']['sha256']
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);paths={}
    for name,reference in frozen['references'].items():
        path=Path(reference['directory']);paths[name]=path
        for file,digest in reference['sha256'].items():assert sha(path/file)==digest
    paths.update(delayed_normal=a.normal.resolve(),delayed_local=a.local.resolve())
    cases={name:inspect(path,name.split('_')[0]) for name,path in paths.items()}
    for condition in ('normal','local'):
        single,eager,delayed=(paths[p+'_'+condition] for p in ('single','eager','delayed'))
        for path in (eager,delayed):
            for file in ('topology.json','rdma.json','graphs/g0.w0/wave.json'):
                assert load(path/file)==load(single/file)
        d=cases['delayed_'+condition];s=cases['single_'+condition]
        assert d['fault_drops']==s['fault_drops'] and d['retransmit_payload_bytes']==s['retransmit_payload_bytes']
    # The normal single and delayed control send exactly the same physical work.
    s=load(paths['single_normal']/'result.json');d=load(paths['delayed_normal']/'result.json')
    assert s['packet_capture']['events']==d['packet_capture']['events']
    assert s['service_transport']['native']['source_packets']==d['service_transport']['native']['source_packets']
    effects={}
    for condition in ('normal','local'):
        delayed=cases['delayed_'+condition]['result_ready_ns']
        for baseline in ('single','eager'):
            reference=cases[baseline+'_'+condition]['result_ready_ns']
            effects[condition+'_vs_'+baseline]=dict(wait_change_ns=delayed-reference,
                wait_change_percent=100*(delayed-reference)/reference)
    result=dict(passed=True,cases=cases,effects=effects,normal_single_packets_exactly_equal=True,
        new_network_executions=2,reused_network_executions=4,hedge_after_ns=70000,
        analyzer_sha256=sha(Path(__file__)),new_algorithm_benefit_proven=False,
        scope='Four real routing files; two layers and one Decode step, declared compute and preexisting expert/ready-KV copies; no full request, calibration, throughput or economic claim')
    save(out/'analysis.json',result)
    rows=[]
    labels={'single':'无副本','eager':'立即副本','delayed':'70微秒延迟副本'}
    for condition in ('normal','local'):
        for policy in ('single','eager','delayed'):
            row=cases[policy+'_'+condition]
            rows.append(f'<tr><td>{"正常" if condition=="normal" else "局部故障"}</td><td>{labels[policy]}</td>'
                f'<td>{row["result_ready_ns"]/1000:.3f}</td><td>{row["attempts_finished_ns"]/1000:.3f}</td>'
                f'<td>{row["network_drained_ns"]/1000:.3f}</td><td>{row["execution_attempt_requests"]}</td>'
                f'<td>{row["unique_payload_bytes"]:,}</td><td>{row["fault_drops"]}</td></tr>')
    dn,dl=(cases['delayed_'+c] for c in ('normal','local'));sn,sl=(cases['single_'+c] for c in ('normal','local'))
    savings=sl['result_ready_ns']-dl['result_ready_ns']
    html=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>固定70微秒延迟副本对照</title><style>body{{font:17px/1.7 system-ui;max-width:1150px;margin:40px auto;padding:0 24px;background:#f5f7fb;color:#182238}}h1,h2{{line-height:1.3}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{padding:12px;border:1px solid #ccd5e3;text-align:right}}th:first-child,td:first-child{{text-align:left}}.card{{padding:20px;background:white;border-radius:12px;margin:20px 0}}.small{{font-size:14px;color:#48566b}}</style>
    <h1>延迟启动副本，究竟换来了什么</h1><p>在同一128端点Clos上补齐成熟对照：先等正常任务；70微秒仍未完成，才启动完整备用。</p>
    <div class="card"><h2>先看结果</h2><p>正常时延迟副本结果{dn['result_ready_ns']/1000:.3f}微秒，与无副本相比变化{(dn['result_ready_ns']-sn['result_ready_ns'])/1000:+.3f}微秒；原始逐包记录完全一致，备用没有执行，但它的模型和KV已预留。</p>
    <p>局部故障时结果{dl['result_ready_ns']/1000:.3f}微秒，相对无副本节省{savings/1000:.3f}微秒（{100*savings/sl['result_ready_ns']:.2f}%）。正收益表示这次成熟备用更早提供了结果；这不是新算法贡献。</p></div>
    <h2>六组实际结果</h2><p class="small">时间均为微秒。结果就绪、最后尝试完成和网络排空分开；物理执行请求数包括已启动副本。</p>
    <div style="overflow:auto"><table><tr><th>网络条件</th><th>策略</th><th>结果就绪</th><th>尝试完成</th><th>网络排空</th><th>实际请求执行数</th><th>有效通信字节</th><th>故障丢包</th></tr>{''.join(rows)}</table></div>
    <h2>这次究竟仿真了什么</h2><p>16模拟服务器、8接入/8汇聚/4核心交换机，各层400Gbps、每链路1微秒，两组EP64，每rank每层4专家。4份真实R1-AWQ/MMLU专家选择，原始层3/4、一个Decode步；计算为声明成本。原始路由被投影为BF16通信，不是实机抓包。</p>
    <p>normal不额外注入故障；local只在前20微秒丢弃发往GPU28的数据包，实际6包，ACK等控制保留。所有策略都启用选择性重传和参考DCQCN，全部原始计算、门限和ACK保留。</p>
    <h2>系统实现与资源</h2><p>截止时间由原生ns-3定时事件产生，不拼接两次仿真。正常primary先完成后取消定时器，备用保持NOT_STARTED且不生成ITL；局部故障到70微秒才进入真实LLMServingSim调度队列。已启动尝试继续收尾。</p>
    <p>两条件副本初始KV均预留{dn['standby_memory']['initial_reserved_bytes']:,}字节，正常在{dn['standby_memory']['releases'][0]['time_ns']/1000:.3f}微秒释放，故障在{dl['standby_memory']['releases'][0]['time_ns']/1000:.3f}微秒释放。结束占用0。KV是资源声明，未校验数值等价，也未模拟初态建立成本。</p>
    <h2>对创新方向的约束</h2><p>立即副本用更多工作换取故障时快速结果；延迟副本避免本次正常重复执行，但付出启动等待。新的方法需要在这两类成熟对照之上，同时证明结果等待、后台干扰或容量收益，不能把普通重传或普通延迟副本写成创新。</p>
    <p>后续可研究多任务共享上行时，备用启动如何影响健康任务，以及只靠在线可见确认进度能否减少错误启动。需要事前固定指标与负载，再做对照，当前尚未验证这些新方法。</p>
    <p class="small">两份新网络各一次，四份旧结果只读复用；固定门槛未寻优。没有完整671B推理、GPU校准、数值验证、吞吐、生产故障概率或经济收益结论。<a href="analysis.json">完整分析JSON</a> · <a href="frozen.json">执行前冻结</a></p></html>'''
    (out/'report.html').write_text(html)
    print(json.dumps(dict(passed=True,effects=effects),ensure_ascii=False))

if __name__=='__main__':main()
