"""Independently check raw-source matrices, full gates, winners and late cleanup."""
import argparse
from collections import Counter
import hashlib
from html import escape
import json
from pathlib import Path


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
    with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')


def interval_bytes(ranges):
    end=0;covered=0
    for begin,stop in sorted(ranges):
        assert 0<=begin<stop
        covered+=max(0,stop-max(begin,end));end=max(end,stop)
    return covered


def inspect(run):
    c=load(run/'input.json');r=load(run/'result.json');f=load(run/'frontend.json');report=load(run/'whole_call_report.json')
    assert r['passed'] and r['drained'] and r['gpu_count']==128 and r['service']['service_finished']
    assert r['packet_capture']['complete'] and not r['packet_capture']['omitted_events']
    assert c['whole_call_replication']['policy']=='eager_complete_segment/1' and c['workload']['decode_steps']==1
    assert report==f['whole_call_replication'] and report['all_attempts_retired']
    assert len(report['calls'])==1 and len(c['whole_call_replication']['calls'])==1
    call=next(iter(report['calls'].values()));group_call=c['whole_call_replication']['calls'][0]
    primary=c['parallel']['groups'][group_call['primary_group']];replica=c['parallel']['groups'][group_call['replica_group']]
    assert len(primary)==len(replica)==64 and len(set(primary+replica))==128
    manifest=load(run/c['route_replay']['manifest']['path']);originals={};hashes=set()
    for file in manifest['files']:
        path=run/file['file']['path'];assert sha(path)==file['file']['sha256']
        assert file['file']['sha256'] not in hashes;hashes.add(file['file']['sha256'])
        originals[file['file_id']]=load(path)
    requests={(q['rank_id'],q['slot']):q for q in c['route_replay']['requests']}
    assert len(requests)==len(originals)==report['logical_requests']
    assert report['execution_attempt_requests']==2*len(originals)
    assert all(rank in primary for rank,slot in requests)
    raw=[json.loads(line.removeprefix('MOE_RDMA_EVENT ')) for line in (run/'native.stdout.log').read_text().splitlines()
         if line.startswith('MOE_RDMA_EVENT ')]
    observed=[row for row in raw if row['type']=='OBSERVE']
    assert observed==[json.loads(line) for line in (run/'execution_events.jsonl').read_text().splitlines()]
    times={};retired={}
    for row in observed:
        if row['kind'].startswith('node_'):
            key=row['graph_id'],row['rank'],row['node'],row['kind'];assert key not in times;times[key]=row['time_ns']
        elif row['kind']=='graph_retired':retired[row['graph_id']]=row
    native={w['graph_id']:w for w in r['service']['waves']}
    assert set(native)==set(retired)=={a['graph_id'] for a in call['attempts'].values()}
    waves={g:load(run/'graphs'/g/'wave.json') for g in native};bytes_by_role={};compute_by_role={}
    actual={m['id']:m for m in r['service_transport']['messages']}
    width=c['model']['experts']//64;hidden=c['model']['hidden_size']*2;k=c['model']['top_k']
    aligned=lambda v,unit:((v+unit-1)//unit)*unit
    dispatch=aligned(hidden+aligned(k*8+(1+k)*4,32),64)
    combine=aligned(hidden+aligned(k*8,32),64)
    for role,attempt in call['attempts'].items():
        g=attempt['graph_id'];wave=waves[g];group=wave['rank_ids'];expected=Counter()
        assert native[g]['start_ns']==attempt['submitted_ns']==0
        assert native[g]['finished_ns']==attempt['finished_ns']==retired[g]['time_ns']
        assert len(wave['routes'])==len(c['route_replay']['layer_ids'])==2
        assert len(wave['phase_completion']['gates'])==4
        for layer in wave['routes']:
            assert len(layer['tokens'])==len(requests)
            for token in layer['tokens']:
                source=token['rank'];original=source if role=='primary' else primary[replica.index(source)]
                q=requests[original,token['slot']];entry=q['first_decode_entry'];lid=c['route_replay']['layer_ids'][layer['layer']]
                experts=originals[q['file_id']][entry][str(lid)][0]
                assert token['experts']==experts and token['source']['file_id']==q['file_id']
                assert token['source']['source_entry']==entry and token['source']['source_layer_id']==lid
                for dst in {group[e//width] for e in experts}:
                    if dst!=source:
                        expected[layer['layer'],'dispatch',source,dst]+=dispatch
                        expected[layer['layer'],'combine',dst,source]+=combine
        found={}
        for m in wave['messages']:
            key=m['layer'],m['phase'],m['src'],m['dst'];assert key not in found
            found[key]=m['bytes'];real=actual[m['id']]
            assert all(real[k]==m[k] for k in ('src','dst','bytes'))
            assert real['ready_ns']<=real['receive_ready_ns']<=real['ack_ns']<=attempt['finished_ns']
        assert found==dict(expected);bytes_by_role[role]=sum(found.values())
        compute_by_role[role]=sum(n['duration_ns'] for rows in wave['rank_nodes'].values() for n in rows if n['kind']=='COMP')
        for rank,rows in wave['rank_nodes'].items():
            rank=int(rank)
            for n in rows:
                begin=times[g,rank,n['id'],'node_issue'];end=times[g,rank,n['id'],'node_complete']
                assert end>=begin and end<=attempt['finished_ns']
                assert all(begin>=times[g,rank,d,'node_complete'] for d in n['deps'])
                if n['kind']=='COMP':assert end-begin==n['duration_ns']
        for gate in (x for x in r['service']['phase_completion']['gates'] if x['graph_id']==g):
            assert len(gate['members'])==64 and gate['released_ns']==max(m['arrived_ns'] for m in gate['members'])
            for m in gate['members']:
                assert m['arrived_ns']==times[g,m['rank'],m['node'],'node_issue']
                assert gate['released_ns']==times[g,m['rank'],m['node'],'node_complete']
    assert bytes_by_role['primary']==bytes_by_role['replica']
    assert compute_by_role['primary']==compute_by_role['replica']
    transport=r['service_transport']['native'];ranges={s['id']:[] for s in transport['streams']}
    streams={s['id']:s for s in transport['streams']}
    assert len(streams)==232 and transport['physical_drained'] and transport['closed']
    assert transport['source_packet_columns']==['time_ns','stream','src','dst','nic','sequence','payload_bytes','packet_uid']
    for time,stream,src,dst,nic,seq,size,uid in transport['source_packets']:
        s=streams[stream];assert (src,dst,nic)==(s['src'],s['dst'],s['source_nic'])
        assert 0<=time<=r['finish_ns'] and seq+size<=s['published_bytes']
        ranges[stream].append((seq,seq+size))
    source_rows=Counter((p[0],p[2],p[4],p[1],p[5],p[6],p[7]) for p in transport['source_packets'])
    wire_rows=Counter((p[0],p[1],p[2],p[5],p[7],p[11],p[4]) for p in r['packet_capture']['events']
                      if p[1]<128 and p[3]==0 and p[6]==17)
    assert source_rows==wire_rows
    accepted={s['stream']:s for s in transport['selective_repeat']['states']}
    unique_source_bytes=0
    for stream,s in streams.items():
        coverage=interval_bytes(ranges[stream]);unique_source_bytes+=coverage
        assert coverage==s['published_bytes']==s['submitted_bytes']==s['snd_una']==accepted[stream]['receiver_prefix']
        assert not accepted[stream]['held_ranges'] and not accepted[stream]['reorder_bytes']
    assert unique_source_bytes==sum(bytes_by_role.values())
    source_bytes=sum(p[6] for p in transport['source_packets'])
    assert source_bytes==sum(s['source_payload_bytes'] for s in streams.values())
    topology=load(run/'topology.json');node_counts=dict(Counter(n['kind'] for n in topology['nodes']))
    assert node_counts==dict(gpu=128,tor=8,agg=8,core=4)
    assert all(link['rate_bps']==400_000_000_000 and link['delay_ns']==1000 for link in topology['links'])
    active_ranks={m[k] for m in actual.values() for k in ('src','dst')}
    roles={rank:role for role,group in [('primary',primary),('replica',replica)] for rank in group}
    ecn_by_role=Counter();cnp_by_role=Counter()
    for e in r['packet_capture']['events']:
        if e[1] in roles and e[3]==1 and e[6]==17 and e[9]:ecn_by_role[roles[e[1]]]+=1
    for e in transport['dcqcn_reference']['events']:
        if e['kind']=='cnp_received':cnp_by_role[roles[streams[e['stream']]['src']]]+=1
    winner=min(call['attempts'],key=lambda role:retired[call['attempts'][role]['graph_id']]['sequence'])
    ready=call['attempts'][winner]['finished_ns']
    assert winner==call['winner'] and ready==call['result_ready_ns']==report['result_ready_ns']
    life=[json.loads(line) for line in (run/'whole_call_events.jsonl').read_text().splitlines()]
    assert len(life)==7 and [e['sequence'] for e in life]==list(range(7))
    commits=[e for e in life if e['kind']=='client_result_ready'];assert len(commits)==1
    assert commits[0]['attempt']==winner and commits[0]['time_ns']==ready
    for e in life:
        if 'execution_sequence' in e:
            graph=call['attempts'][e['attempt']]['graph_id']
            assert e['time_ns']==retired[graph]['time_ns'] and e['execution_sequence']==retired[graph]['sequence']
    verification=load(run/'verification.json')
    attempts_end=max(a['finished_ns'] for a in call['attempts'].values())
    assert verification['application_finished_ns']==ready and verification['attempts_finished_ns']==attempts_end
    assert verification['network_drained_ns']==r['finish_ns']>=attempts_end
    return dict(passed=True,source_kind=manifest['source']['kind'],unique_sources=len(originals),logical_requests=report['logical_requests'],
        execution_attempt_requests=report['execution_attempt_requests'],messages=len(actual),packet_events=len(r['packet_capture']['events']),
        fault_drops=r.get('data_loss',{}).get('dropped_packets',0),winner=winner,result_ready_ns=ready,
        topology_node_counts=node_counts,servers=len({p['server'] for p in topology['nic_ports']}),
        active_data_ranks=len(active_ranks),source_data_packets=len(transport['source_packets']),
        source_payload_bytes_including_retransmits=source_bytes,unique_source_payload_bytes=unique_source_bytes,
        extra_source_payload_bytes=source_bytes-unique_source_bytes,
        fault_window_arrivals=r.get('data_loss',{}).get('window_packets',0),
        target_all_time_arrivals=r.get('data_loss',{}).get('eligible_destination_data_packets',0),
        primary_finished_ns=call['attempts']['primary']['finished_ns'],replica_finished_ns=call['attempts']['replica']['finished_ns'],
        attempts_finished_ns=attempts_end,network_drained_ns=r['finish_ns'],payload_bytes_by_attempt=bytes_by_role,
        declared_compute_ns_by_attempt=compute_by_role,
        ecn_data_received_by_attempt=dict(ecn_by_role),cnp_received_by_attempt=dict(cnp_by_role),
        phase_release_ns={g['graph_id']+'/'+g['id']:g['released_ns'] for g in r['service']['phase_completion']['gates']},
        scope='same-run complete-attempt timing, not net gain versus no redundancy',
        sources_sha256={str(p.relative_to(run)):sha(p) for p in [run/'input.json',run/'topology.json',run/'rdma.json',run/'result.json',run/'whole_call_report.json',run/'whole_call_events.jsonl']}),c


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--normal',type=Path,required=True)
    p.add_argument('--local',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    normal,c0=inspect(a.normal);local,c1=inspect(a.local)
    assert c0==c1 and normal['sources_sha256']['topology.json']==local['sources_sha256']['topology.json']
    p0=load(a.normal/'rdma.json');p1=load(a.local/'rdma.json');fault=p1.pop('data_loss')
    assert p0==p1 and fault['start_ns']==0 and fault['end_ns']==20000 and fault['numerator']==fault['denominator']==1
    assert normal['fault_drops']==0 and local['fault_drops']>0
    report=dict(passed=True,normal=normal,local=local,network_reexecuted=False,
        conclusion='whole client readiness and physical attempt cleanup separated; no no-redundancy control or net recovery-benefit claim')
    save(out/'verification.json',report)
    rows=[]
    for name,r in [('正常网络',normal),('局部短时数据故障',local)]:
        values=[name,r['winner'],*[f'{r[k]/1000:.3f}' for k in ('result_ready_ns','primary_finished_ns','replica_finished_ns','network_drained_ns')],str(r['fault_drops'])]
        rows.append('<tr>'+''.join('<td>'+escape(v)+'</td>' for v in values)+'</tr>')
    bars=[]
    for i,(name,r) in enumerate([('正常网络',normal),('局部故障',local)]):
        top=40+i*115
        bars.append(f'<text x="10" y="{top+15}">{name}</text>')
        for j,(role,label,color) in enumerate([('primary','原尝试','#4474b5'),('replica','副本','#32957d')]):
            y=top+j*34;x=150+650*r[role+'_finished_ns']/150000
            bars.append(f'<rect x="150" y="{y}" width="{x-150:.2f}" height="23" fill="{color}" rx="3"/><text x="{x+7:.2f}" y="{y+17}">{label} {r[role+"_finished_ns"]/1000:.3f}µs</text>')
        for key,color,dash in [('result_ready_ns','#a83349',''),('network_drained_ns','#586675','5 4')]:
            x=150+650*r[key]/150000
            bars.append(f'<line x1="{x:.2f}" x2="{x:.2f}" y1="{top-5}" y2="{top+63}" stroke="{color}" stroke-width="2" stroke-dasharray="{dash}"/>')
    timeline='<svg viewBox="0 0 1020 290" role="img" aria-label="正常与故障下两份尝试的真实完成时间">'+''.join(bars)+'<text x="150" y="275">共同从0开始；红线：结果可用，灰虚线：后台与网络排空。条长单位为仿真时间。</text></svg>'
    text='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>完整副本结果与真实清理</title><style>body{font:17px/1.7 system-ui;max-width:1100px;margin:36px auto;padding:0 24px;color:#142a40;background:#f5f8fc}table{border-collapse:collapse;background:white;width:100%;font-size:15px}th,td{padding:10px;border:1px solid #c7d3df;text-align:left}h1{font-size:28px}</style><h1>一个完整副本完成，才可提交结果</h1><p>128个模拟端点、Clos、两个互斥EP64完整副本，各层400Gbps、每链路1µs。四份真实R1-AWQ/MMLU原件，原层3/4、一个Decode步；4个逻辑请求、8次显式执行，不是8份独立真实数据。</p><p>完整结果只来自实际整图退休。较晚尝试的消息、ACK、计算和阶段门限继续执行，没有伪造原任务完成。</p><table><tr><th>条件</th><th>赢家</th><th>逻辑就绪 µs</th><th>原尝试完成 µs</th><th>副本完成 µs</th><th>网络排空 µs</th><th>故障丢包</th></tr>'''+''.join(rows)+'''</table><p>这是成熟eager完整副本基线的功能验证。两个副本立即执行，额外工作真实计入；初始等价ready-KV与完整权重副本为声明条件，没有模拟建立成本或张量客户端交付。当前只模拟有限MoE片段，不是完整671B模型或GPU测量。</p><p><strong>上述时间差不是相对原地SR或成熟延迟备用的净收益。</strong>还缺少相同资源的无冗余控制、延迟启动、正常额外成本及背景失败研究。不能将已有方法重新命名为创新。</p><p>逐源字节矩阵、原门限、全部尝试完成及实际回调核验见<a href="verification.json">核验记录</a>。</p></html>'''
    details=f'''<h2>normal和local到底差什么</h2><p>两组使用相同任务、部署、选择性重传与参考拥塞控制。normal不额外注入故障；local只在0～20µs丢弃发往GPU28的DATA包，不丢ACK/NACK/PFC。实际窗口内6/6包丢弃，目标端全观察期共14个DATA到达；不是全网100%丢包或GPU整机失效。</p>
    {timeline}<h2>额外成本与仍缺的对照</h2><p>16台模拟服务器、8个接入/8个汇聚/4个核心交换机；128端点中70个实际参与数据通信，不是全卡满载。两份完整EP64专家副本预先存在，每rank每层4专家。</p>
    <p>每份尝试有效负载{normal['payload_bytes_by_attempt']['primary']:,}B，两份合计{normal['unique_source_payload_bytes']:,}B，是一份相同片段的2倍计划负载；两份声明计算工作也分别为{normal['declared_compute_ns_by_attempt']['primary']:,}ns（各rank累计工作量，不是任务耗时）。故障另产生{local['extra_source_payload_bytes']:,}B重传，源数据包由{normal['source_data_packets']}增至{local['source_data_packets']}。</p>
    <p>故障中结果在64.659µs可用，后台仍持续72.170µs。这是同一次运行中结果与收尾的间隔，<strong>不能称为相对无冗余的收益</strong>。正常网络也付出双份流量与计算；完整模型副本和等价KV的建立成本未模拟。</p>
    <h2>为什么故障场景的健康副本反而更早</h2><p>副本首层Dispatch门限两组均18.022µs，随后开始分化；副本ECN标记接收从20降为7，CNP接收也从20降为7，最终完成68.863→64.659µs。它与受损组暂缓推进、共享竞争改变相符，但尚非对该原因的独立因果对照，更不能解释成丢包普遍有利。两组的最快结果差2.935µs不用于宣称恢复收益。</p>
    <p>正常组两份尝试在68.863µs已完成，117.091µs才排空；原始包记录显示最后仍有拥塞通知包CNP到达。客户端结果无需等这段收尾。故障组最后ACK为136.799µs，尝试与服务在136.829µs退出。</p>
    <h2>下一项需要回答的问题</h2><p>先增加相同128端点、相同原始请求和相同SR配置的无冗余控制，再比较立即双份执行与成熟延迟备用。分别核算正常额外开销、故障尾时延、重复字节、计算与KV占用、其他任务受影响程度；只有在强基线之上仍有稳定且有意义的空间，才进入新策略。</p>'''
    text=text.replace('</table>','</table>'+details,1)
    (out/'report.html').write_text(text)
    print(json.dumps(dict(passed=True,normal_ready_ns=normal['result_ready_ns'],local_ready_ns=local['result_ready_ns'],local_drain_ns=local['network_drained_ns'])))


if __name__=='__main__':main()
