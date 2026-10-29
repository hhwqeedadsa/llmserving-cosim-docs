"""Independent packet accounting and paired comparisons for the frozen study."""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
import hashlib
import html
import json
from pathlib import Path


def read(path):
    if path.stat().st_size>128*2**20:raise ValueError('analysis input exceeds 128MiB')
    return json.loads(path.read_bytes())


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def check(directory,reference,adaptive,initial):
    r=read(directory/'result.json');p=read(directory/'rdma.json');c=read(directory/'input.json')
    expected=deepcopy(read(reference/'rdma.json'));expected['recovery']['initial_rto_ns']=initial
    if adaptive:expected['recovery']['rtt_estimator']=dict(kind='karn',min_rto_ns=20000,granularity_ns=1)
    assert p==expected,'unregistered protocol change'
    assert c==read(reference/'input.json'),'request/model input changed'
    assert sha(directory/'topology.json')==sha(reference/'topology.json'),'topology changed'
    cap=r['packet_capture'];assert cap['complete'] and cap['omitted_events']==0
    assert r['passed'] and read(directory/'verification.json')['passed']
    assert r['recovery']['complete'] and not r['recovery']['failed']
    assert read(directory/'verification.json')['recovery']['reconstructed']
    flows={m['id']:m for m in r['messages']};meta={};wave_hashes={}
    for path in sorted((directory/'graphs').glob('*/wave.json')):
        graph=read(path);wave_hashes[graph['graph_id']]=sha(path)
        assert sha(path)==sha(reference/'graphs'/graph['graph_id']/'wave.json'),'work graph changed'
        for m in graph['messages']:
            assert m['id'] not in meta;meta[m['id']]=m
    assert flows.keys()==meta.keys() and len(flows)==790 and len(wave_hashes)==4
    prefix=defaultdict(int);feedback=defaultdict(int);confirmed_at={};seen=set();tx=defaultdict(Counter)
    last=-1;eligible=window=dropped=0;loss=p.get('data_loss');n=r['gpu_count']
    for values in cap['events']:
        packet=dict(zip(cap['columns'],values))
        t,node,event,identity,protocol=(packet[k] for k in ('time_ns','node','event','message_id','ip_protocol'))
        assert last<=t<=r['finish_ns'];last=t
        if loss and node<n and protocol==17 and event in (1,2) and (not loss['nodes'] or node in loss['nodes']):
            eligible+=1;want=False
            if loss['start_ns']<=t<loss['end_ns']:
                want=(window+loss['offset'])%loss['denominator']<loss['numerator'];window+=1
            assert (event==2)==want;dropped+=want
        if identity not in meta:continue
        m=meta[identity];seq=packet['sequence'];size=packet['data_payload_bytes']
        if protocol==17:
            assert seq%p['payload_bytes']==0 and size==min(p['payload_bytes'],m['bytes']-seq) and size>0
            if node==m['src'] and event==0:
                tx[identity]['packets']+=1;tx[identity]['payload']+=size
                if (identity,seq) not in seen:tx[identity]['unique']+=size
                seen.add((identity,seq))
            if node==m['dst'] and event==1 and seq==prefix[identity]:prefix[identity]+=size
        elif protocol in (252,253) and node==m['src'] and event==1:
            assert 0<=seq<=prefix[identity]
            feedback[identity]=max(feedback[identity],seq)
            if feedback[identity]==m['bytes']:confirmed_at.setdefault(identity,t)
    for identity,m in flows.items():
        assert all(m[k]==meta[identity][k] for k in ('src','dst','bytes','plane'))
        assert prefix[identity]==feedback[identity]==m['bytes']
        assert tx[identity]['payload']==m['source_data_payload_bytes']
        assert tx[identity]['unique']==m['source_unique_data_payload_bytes']==m['bytes']
        assert tx[identity]['packets']==m['source_data_packets']
        assert confirmed_at[identity]==m['ack_ns']
    if loss:
        native=r['data_loss']
        assert (eligible,window,dropped)==tuple(native[k] for k in ('eligible_destination_data_packets','window_packets','dropped_packets'))
    recovery=r['recovery'];codes=recovery['event_codes'];counts=Counter(row[1] for row in recovery['events'])
    frontend=read(directory/'frontend.json');history=frontend['history'];states=recovery['states']
    application_finished=max(h['finished_ns'] for h in history)
    assert r['service']['service_finished'] and r['service']['active_graphs']==0
    assert application_finished==max(w['finished_ns'] for w in r['service']['waves'])
    finish_decisions=[entry for entry in frontend['transcript'] if entry['reply']['service_finished']]
    assert len(finish_decisions)==1
    final=finish_decisions[0]
    assert final['event']['now_ns']==final['reply']['now_ns']==application_finished<=r['finish_ns']
    logical=sum(m['bytes'] for m in flows.values());assert logical==13533184
    return dict(run=str(directory),passed=True,finish_ns=r['finish_ns'],messages=len(flows),logical_bytes=logical,
        application_finished_ns=application_finished,network_drained_ns=r['finish_ns'],
        post_application_drain_ns=r['finish_ns']-application_finished,
        source_tx_payload_bytes=sum(v['payload'] for v in tx.values()),
        duplicate_payload_bytes=sum(v['payload']-v['unique'] for v in tx.values()),
        timeout_retransmissions=counts[codes['timeout_retransmit']],
        rtt_samples=sum(s.get('rtt',{}).get('samples',0) for s in states),
        rtt_discarded_probes=sum(s.get('rtt',{}).get('discarded_probes',0) for s in states),
        messages_with_rtt_samples=sum(s.get('rtt',{}).get('samples',0)>0 for s in states),
        packet_events_checked=len(cap['events']),all_confirmation_times_checked=True,
        group_finished_ns={str(i):max(h['finished_ns'] for h in history if h['group_index']==i) for i in (0,1)},
        loss=None if not loss else dict(window_packets=window,dropped_packets=dropped,eligible_packets=eligible),
        result_sha256=sha(directory/'result.json'),wave_sha256=wave_hashes)


def analyze(study,baseline,output):
    if output.exists():raise ValueError('new result directory required')
    cases={}
    for name,old in [('fixed40-normal','bottleneck25'),('fixed40-loss50','loss50')]:
        cases[name]=check(baseline/old,baseline/old,False,40000)
    for name,old,adaptive in [('fixed80-normal','bottleneck25',False),('karn80-normal','bottleneck25',True),
                              ('fixed80-loss50','loss50',False),('karn80-loss50','loss50',True)]:
        cases[name]=check(study/name,baseline/old,adaptive,80000)
    pairs={}
    for mechanism in ('fixed40','fixed80','karn80'):
        normal,fault=(cases[mechanism+'-'+s] for s in ('normal','loss50'))
        pairs[mechanism]=dict(fault_extra_ns=fault['application_finished_ns']-normal['application_finished_ns'],
            fault_relative_to_own_normal_pct=100*(fault['application_finished_ns']/normal['application_finished_ns']-1))
    comparisons={}
    for base,target in [('fixed40','fixed80'),('fixed80','karn80')]:
        comparisons[base+'-to-'+target]={}
        for suffix in ('normal','loss50'):
            a,b=cases[base+'-'+suffix],cases[target+'-'+suffix]
            comparisons[base+'-to-'+target][suffix]=dict(time_change_ns=b['application_finished_ns']-a['application_finished_ns'],
                time_change_pct=100*(b['application_finished_ns']/a['application_finished_ns']-1),
                network_drain_time_change_ns=b['network_drained_ns']-a['network_drained_ns'],
                network_drain_time_change_pct=100*(b['network_drained_ns']/a['network_drained_ns']-1),
                duplicate_payload_change_bytes=b['duplicate_payload_bytes']-a['duplicate_payload_bytes'])
    verdict=dict(schema_version='moe-service-recovery-baselines-evaluation/2',passed=True,
        primary_metric='application_finished_ns: final frontend service_finished COMMIT and last actual graph completion',
        new_network_runs=4,reused_reference_runs=2,hardware_calibrated=False,novel_algorithm_gain_claimed=False,
        scope='15 real routing requests, 320 physical endpoints, finite two-layer/two-step ready-KV segment',
        packet_events_checked=sum(c['packet_events_checked'] for c in cases.values()),cases=cases,
        fault_pairs=pairs,mechanism_comparisons=comparisons,
        source_sha256={name:sha(Path(__file__).parent/name) for name in ('run.py','analyze.py','protocol.md')})
    lines=['# 320端点真实路由：恢复强基线实验','',
        '本轮只比较已有固定超时与Karn估计，不是新算法收益。四个新网络各一次，两个旧固定40µs条件只读复用。',
        '', '| 条件 | 业务完成/µs | 网络排空/µs | 超时次数 | 重复载荷/B | 有效RTT样本 |',
        '|---|---:|---:|---:|---:|---:|']
    for name,c in cases.items():
        lines.append(f"| {name} | {c['application_finished_ns']/1000:.3f} | {c['network_drained_ns']/1000:.3f} | {c['timeout_retransmissions']} | {c['duplicate_payload_bytes']:,} | {c['rtt_samples']} |")
    lines+=['','以下时间变化按业务观察窗口完成计算，为正表示更慢。网络排空另列；重复载荷减少不能替代业务收益。','']
    for name,values in comparisons.items():
        for suffix,c in values.items():
            lines.append(f"- {name} / {suffix}：完成变化 {c['time_change_ns']/1000:+.3f}µs（{c['time_change_pct']:+.2f}%），重复载荷变化 {c['duplicate_payload_change_bytes']:+,}B。")
    lines+=['','每个故障条件相对自身正常条件的额外等待：','']
    for name,c in pairs.items():lines.append(f"- {name}：{c['fault_extra_ns']/1000:.3f}µs。")
    lines+=['','输入、拓扑及四张工作图保持一致；全部消息从数据包独立核对源字节、接收前缀和确认完成时刻。Karn状态由公共核验逐事件重放。',
        '', '结果只覆盖当前有限Decode窗口和GBN主服务。没有运行完整R1、生产请求p99或GPU校准；主服务选择重传仍未贯通，剩余差距不能直接称新方法可优化空间。',
        '', '下一步先据本结果选择公平参考，再检查健康任务的实际共享资源与关键依赖；若普通限速/隔离已经消除差距，应关闭候选。']
    output.mkdir(parents=True)
    (output/'verification.json').write_text(json.dumps(verdict,ensure_ascii=False,indent=2)+'\n')
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    table=''.join('<tr><td>'+html.escape(name)+'</td>'+''.join('<td>'+html.escape(str(value))+'</td>' for value in
        [f"{c['application_finished_ns']/1000:.3f}",f"{c['network_drained_ns']/1000:.3f}",c['timeout_retransmissions'],c['duplicate_payload_bytes'],c['rtt_samples']])+'</tr>' for name,c in cases.items())
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>恢复强基线实验</title><style>body{max-width:1100px;margin:40px auto;padding:0 24px;font:17px/1.8 system-ui;color:#182333;background:#f8fafc}table{width:100%;border-collapse:collapse;background:white}th,td{border-bottom:1px solid #d7dee8;padding:10px;text-align:right}th:first-child,td:first-child{text-align:left}pre{white-space:pre-wrap;background:white;padding:24px}</style><h1>320端点真实路由：恢复强基线</h1><p>4次新网络 · 2项只读旧参考 · 业务完成与网络排空分列 · 未声明创新收益</p><table><tr><th>条件</th><th>业务完成/µs</th><th>网络排空/µs</th><th>超时</th><th>重复载荷/B</th><th>RTT样本</th></tr>'+table+'</table><pre>'+html.escape('\n'.join(lines[2:]))+'</pre></html>'
    (output/'report.html').write_text(page)
    print(json.dumps(dict(passed=True,packet_events_checked=verdict['packet_events_checked'],comparisons=comparisons),indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('study','baseline-root','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();analyze(args.study,args.baseline_root,args.output)
