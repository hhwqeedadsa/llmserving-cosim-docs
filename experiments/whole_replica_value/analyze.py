"""Matched whole-replica value analysis over saved public runs; no execution."""
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


def inspect(path,eager):
    c=load(path/'input.json');r=load(path/'result.json');f=load(path/'frontend.json');v=load(path/'verification.json')
    assert r['passed'] and r['drained'] and r['gpu_count']==128 and r['service']['service_finished'] and v['passed']
    assert r['packet_capture']['complete'] and r['packet_capture']['omitted_events']==0
    assert c['workload']['decode_steps']==1 and len(c['parallel']['groups'])==2
    assert all(len(group)==64 for group in c['parallel']['groups'])
    if not eager:
        assert c['route_replay']['request_layout']=='sparse_active_groups/1'
        assert f['admitted_group_indices']==[0] and len(f['requests'])==4 and len(f['history'])==1
    else:assert len(f['requests'])==8 and len(f['history'])==2
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
    assert set(retired)==({'g0.w0','g1.w0'} if eager else {'g0.w0'})
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
        assert native[graph]['start_ns']==0 and native[graph]['finished_ns']==retired[graph]['time_ns']
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
        logical_requests=4,execution_attempt_requests=len(f['requests']),messages=len(messages),packet_events=len(r['packet_capture']['events']),
        result_ready_ns=ready,attempts_finished_ns=max(e['time_ns'] for e in retired.values()),network_drained_ns=r['finish_ns'],
        unique_payload_bytes=sum(payload.values()),source_payload_bytes=source_bytes,source_data_packets=len(transport['source_packets']),
        retransmit_payload_bytes=source_bytes-sum(payload.values()),declared_compute_work_ns=sum(compute.values()),
        fault_drops=loss.get('dropped_packets',0),fault_window_arrivals=loss.get('window_packets',0),phase_member_arrivals=member_count,
        sha256={n:sha(path/n) for n in ('input.json','topology.json','rdma.json','result.json','frontend.json','verification.json')})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('frozen','normal','local','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();frozen=load(a.frozen);assert frozen['passed']
    assert sha(Path(__file__).with_name('protocol.md'))==frozen['method_sha256']
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    rows={};configs={};paths={}
    for condition,control in [('normal',a.normal),('local',a.local)]:
        for policy,path in [('single',control),('eager',Path(frozen['references'][condition]['directory']))]:
            key=condition+'_'+policy;paths[key]=path;rows[key]=inspect(path,policy=='eager');configs[key]=load(path/'input.json')
        c=deepcopy(configs[condition+'_eager']);c.pop('whole_call_replication');c['route_replay']['request_layout']='sparse_active_groups/1'
        assert c==configs[condition+'_single']
        assert load(control/'graphs/g0.w0/wave.json')==load(paths[condition+'_eager']/'graphs/g0.w0/wave.json')
        for name in ('topology.json','rdma.json'):
            assert rows[condition+'_single']['sha256'][name]==rows[condition+'_eager']['sha256'][name]
        for name,digest in frozen['references'][condition]['sha256'].items():assert sha(paths[condition+'_eager']/name)==digest
        assert rows[condition+'_single']['fault_drops']==rows[condition+'_eager']['fault_drops']
        assert rows[condition+'_eager']['unique_payload_bytes']==2*rows[condition+'_single']['unique_payload_bytes']
        assert rows[condition+'_eager']['declared_compute_work_ns']==2*rows[condition+'_single']['declared_compute_work_ns']
    assert configs['normal_single']==configs['local_single'] and configs['normal_eager']==configs['local_eager']
    assert rows['normal_single']['fault_drops']==0 and rows['local_single']['fault_drops']>0
    sn,en,sf,ef=[rows[k]['result_ready_ns'] for k in ('normal_single','normal_eager','local_single','local_eager')]
    overhead=en-sn;saving=sf-ef
    threshold=overhead/(overhead+saving) if overhead>0 and saving>0 else None
    outcomes=dict(normal_overhead_ns=overhead,normal_overhead_percent=100*overhead/sn,
        local_saving_ns=saving,local_saving_percent=100*saving/sf,
        two_condition_expected_latency_break_even_probability=threshold,
        probability_scope='Illustrative mixture of these two exact deterministic conditions; no measured production fault rate, throughput, confidence interval or resource-inclusive economic break-even',
        mature_delayed_hedge_measured=False,new_algorithm_benefit_proven=False)
    result=dict(passed=True,cases=rows,outcomes=outcomes,normalization='same four logical requests, 128 endpoints and two EP64 deployment; redundant physical work differs',
        graph_and_input_match_verified=True,new_network_executions=2,reused_eager_networks=2,
        frozen_sha256=sha(a.frozen),analyzer_sha256=sha(Path(__file__)))
    save(out/'analysis.json',result)
    table=[]
    for key,label in [('normal_single','正常·无副本'),('normal_eager','正常·立即双副本'),('local_single','故障·无副本'),('local_eager','故障·立即双副本')]:
        r=rows[key];values=[label,f"{r['result_ready_ns']/1000:.3f}",f"{r['attempts_finished_ns']/1000:.3f}",f"{r['network_drained_ns']/1000:.3f}",f"{r['source_payload_bytes']:,}",str(r['fault_drops'])]
        table.append('<tr>'+''.join('<td>'+escape(v)+'</td>' for v in values)+'</tr>')
    text=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>副本收益与代价</title><style>body{{font:17px/1.75 system-ui;color:#183047;background:#f3f7fb;max-width:1100px;margin:32px auto;padding:0 22px}}h1{{font-size:30px}}table{{border-collapse:collapse;background:white;width:100%;font-size:15px}}th,td{{border:1px solid #cbd6e0;padding:10px;text-align:left}}.table{{overflow-x:auto}}.finding{{padding:16px 20px;background:#e3edf8;border-left:5px solid #3974b2}}strong{{color:#8c3246}}</style><h1>副本能抗故障，也会拖慢正常任务</h1>
    <p class="finding">当前条件下，正常网络多等{overhead/1000:.3f}µs（{100*overhead/sn:.2f}%）；局部故障少等{saving/1000:.3f}µs（{100*saving/sf:.2f}%）。这是成熟立即副本相对无副本SR的有限片段对照；<strong>尚未证明新算法收益。</strong></p>
    <h2>我们具体在比较什么</h2><p>同一128端点Clos、16模拟服务器、8接入/8汇聚/4核心交换机、两组EP64、各层400Gbps、1µs/link。四份真实R1-AWQ/MMLU原件、两层、一个Decode步、相同SR/DCQCN和原尝试图。无副本只运行组0，组1保留部署但不虚构请求或KV；双副本立即执行两组。局部故障只在0～20µs丢弃发往GPU28的数据，控制包保留。两种策略实际都丢6包。</p>
    <div class="table"><table><tr><th>条件与策略</th><th>结果可用 µs</th><th>尝试结束 µs</th><th>网络排空 µs</th><th>源实际发送 B</th><th>丢包</th></tr>{''.join(table)}</table></div>
    <h2>正常时付出了什么</h2><p>唯一有效通信由{rows['normal_single']['unique_payload_bytes']:,}B变为{rows['normal_eager']['unique_payload_bytes']:,}B，声明计算工作由{rows['normal_single']['declared_compute_work_ns']:,}ns变为{rows['normal_eager']['declared_compute_work_ns']:,}ns，都是两份工作。计算数字为各rank累计，不能当成任务耗时。数据活跃rank由{rows['normal_single']['active_data_ranks']}变为{rows['normal_eager']['active_data_ranks']}；物理端点始终128。副本额外KV的存在有声明，但数值等价、模型/KV建立与客户端张量传输未模拟。</p>
    <h2>故障时省下什么，又没省下什么</h2><p>结果就绪从{sf/1000:.3f}降到{ef/1000:.3f}µs；这是此次固定故障的条件收益。受损执行仍继续，全部尝试及网络仍到{rows['local_eager']['network_drained_ns']/1000:.3f}µs才结束。两种策略都额外重传{rows['local_single']['retransmit_payload_bytes']:,}B；副本没有修好原连接，也没有取消后台开销。</p>
    <h2>什么时候值得做：只能给出有条件估算</h2><p>若假定所有任务只分为本次normal/local两类，故障概率为p，并且只优化这段结果等待，那么令两条件线性加权，临界p约为{100*threshold:.2f}%。低于这个值时，本轮立即副本的正常代价占优；高于它时，故障节省才在这一简化平均值中占优。</p><p><strong>这不是生产故障率、经济盈亏点或通用阈值。</strong>只各执行一次，没有置信区间；没有其他独立请求、吞吐、完整推理、校准GPU、容量机会成本或初态建立开销。不能据此直接建议生产部署。</p>
    <h2>对创新方向的约束</h2><p>当前可研究的取舍已经明确：正常时避免无效重复，故障时尽早获得完整结果，同时减少后台工作对共享网络的干扰。下一步必须先实现并比较成熟延迟备用；只有在相同信息、资源和完成语义下仍能降低正常代价或故障尾延迟，才构成进一步新策略的证据。选择性重传和立即双副本本身均为既有方法。</p>
    <p>四个案例的原件、整个primary图、协议、拓扑、全部门限/ACK与逐源包已核对；两个eager结果直接复用，没有重跑。<a href="analysis.json">机器可读核算与边界</a></p></html>'''
    (out/'report.html').write_text(text)
    print(json.dumps(dict(passed=True,**outcomes)))


if __name__=='__main__':main()
