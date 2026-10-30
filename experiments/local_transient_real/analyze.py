"""Offline evaluation of frozen tasks, actual wire traffic and service times."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import html
import json
from pathlib import Path

from moe_ub_cosim.scaleout_arrivals import reconstruct
from moe_ub_cosim.scaleout_data_loss import audit as loss_audit
from moe_ub_cosim.service_terminal import service_terminal

CASES=('gbn-normal','gbn-local','sr-normal','sr-local')


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
    with p.open('x') as f:json.dump(x,f,ensure_ascii=False,indent=2);f.write('\n')


def analyze_case(directory,prepared):
    result=load(directory/'result.json');frontend=load(directory/'frontend.json')
    timing=service_terminal(result,frontend,require_complete=True)
    waves=[load(p) for p in sorted((directory/'graphs').glob('*/wave.json'))]
    if {w['graph_id'] for w in waves}!={'g0.w0','g0.w1','g1.w0','g1.w1'}:raise ValueError('missing graph')
    for wave in waves:
        if wave!=load(prepared/'planned'/(wave['graph_id']+'.json')):raise ValueError('workload changed')
    native=result['service_transport']['native'];config=load(directory/'input.json')
    summary,rows,endpoints,packets,curve=reconstruct(result,waves,'g0.w0',0,'dispatch')
    other,_,_,_,other_curve=reconstruct(result,waves,'g1.w0',0,'dispatch')
    streams={s['id']:s for s in native['streams']};groups=config['parallel']['groups']
    stream_group={sid:next(i for i,g in enumerate(groups) if s['src'] in g) for sid,s in streams.items()}
    drops=Counter();control=Counter();shared=defaultdict(lambda:Counter());repeated=Counter()
    capture=result['packet_capture']
    for values in capture['events']:
        p=dict(zip(capture['columns'],values));sid=p['message_id'];node=p['node']
        if p['event']==2 and p['ip_protocol']==17 and sid in streams:drops[sid]+=1
        if p['event']==0:
            control[str(p['ip_protocol'])]+=1
            if sid in streams and node>=result['gpu_count']:
                shared[node,p['port']][stream_group[sid]]+=p['model_bytes']
    by_group=[]
    for gi,g in enumerate(groups):
        selected=[r for r in rows if r['group_index']==gi]
        history=[h for h in frontend['history'] if h['group_index']==gi]
        by_group.append(dict(group=gi,rank_ids=g,start_ns=min(h['start_ns'] for h in history),
            finished_ns=max(h['finished_ns'] for h in history),messages=len(selected),
            logical_bytes=sum(r['logical_bytes'] for r in selected),
            source_payload_bytes=sum(r['tx_payload_bytes'] for r in selected),
            repeated_payload_bytes=sum(r['tx_duplicate_payload_bytes'] for r in selected),
            accepted_bytes=sum(r['accepted_bytes'] for r in selected),
            direct_data_drop_events=sum(drops[sid] for sid in streams if stream_group[sid]==gi),
            graphs=[dict(graph_id=h['graph_id'],start_ns=h['start_ns'],finished_ns=h['finished_ns']) for h in history]))
    if by_group[1]['direct_data_drop_events']:raise ValueError('comparison group directly lost data')
    ports=[dict(node=n,port=p,group0_model_bytes=c[0],group1_model_bytes=c[1])
        for (n,p),c in sorted(shared.items()) if c[0] and c[1]]
    if not ports:raise ValueError('groups did not use any common directed switch exit')
    if directory.name.endswith('local'):
        fault=load(directory/'rdma.json')['data_loss'];expected=load(prepared/'hotspot.json')['rank']
        if fault['nodes']!=[expected] or fault['start_ns']!=0 or fault['end_ns']!=100000:
            raise ValueError('fault location/window changed')
        if not loss_audit(result)['dropped_packets']:raise ValueError('local fault never hit')
    drop_summary=result.get('switch_drop_capture',{})
    return dict(timing=timing,groups=by_group,arrival_audit=summary,other_slice=other['slice'],
        data_loss=loss_audit(result),source_data_packets=sum(s['source_data_packets'] for s in streams.values()),
        source_payload_bytes=sum(s['source_payload_bytes'] for s in streams.values()),
        packet_events=len(capture['events']),recovery=native['recovery'].get('counters',{}),
        switch_drop_counts=drop_summary.get('all_switch_reason_counts'),
        shared_directed_exits=ports,all_hop_tx_by_protocol=dict(control),
        identities=dict(result_sha256=sha(directory/'result.json'),frontend_sha256=sha(directory/'frontend.json'))),rows,endpoints,curve,other_curve


def analyze(root,output):
    output.mkdir(parents=True,exist_ok=False);prepared=root/'prepared';manifest=load(prepared/'preparation.json')
    for name,digest in manifest['input_hashes'].items():
        if sha(prepared/name)!=digest:raise ValueError('frozen input hash changed')
    cases={}
    for case in CASES:
        info,rows,endpoints,curve,other=analyze_case(root/case,prepared);cases[case]=info
        for name,values in (('messages',rows),('endpoints',endpoints),('group0-dispatch',curve),('group1-dispatch',other)):
            with (output/(case+'-'+name+'.csv')).open('x',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=list(values[0]));writer.writeheader();writer.writerows(values)
    contrasts={}
    for mode in ('gbn','sr'):
        clean,local=(cases[mode+'-'+suffix] for suffix in ('normal','local'))
        contrasts[mode]=dict(application_extra_ns=local['timing']['application_finished_ns']-clean['timing']['application_finished_ns'],
            group0_extra_ns=local['groups'][0]['finished_ns']-clean['groups'][0]['finished_ns'],
            group1_extra_ns=local['groups'][1]['finished_ns']-clean['groups'][1]['finished_ns'],
            group1_direct_drops=local['groups'][1]['direct_data_drop_events'],
            extra_source_payload_bytes=local['source_payload_bytes']-clean['source_payload_bytes'])
    report=dict(schema_version='moe-local-transient-analysis/1',preparation_sha256=sha(prepared/'preparation.json'),
        cases=cases,contrasts=contrasts,
        scope='finite real-route communication segment; unknown remote revision; reference compute and deployment; not production gain',
        interpretation='extra waiting and GBN/SR differences are measured costs, not demonstrated novel or eliminable gain')
    save(output/'analysis.json',report)
    columns=('条件','服务完成 µs','网络排空 µs','组0完成 µs','组1完成 µs','重复载荷 B','直接丢弃数据包')
    table=[]
    for name,c in cases.items():
        row=[name,*[f'{x/1000:.3f}' for x in (c['timing']['application_finished_ns'],c['timing']['network_drained_ns'],
             c['groups'][0]['finished_ns'],c['groups'][1]['finished_ns'])],
             str(sum(g['repeated_payload_bytes'] for g in c['groups'])),str(c['data_loss'].get('dropped_packets',0))]
        table.append('<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in row)+'</tr>')
    text='<html lang="zh-CN"><meta charset="utf-8"><title>局部短时故障与共享网络</title><style>body{max-width:1150px;margin:40px auto;font:17px/1.8 sans-serif;color:#18263b}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd5df;padding:10px}th{background:#edf3f8}pre{white-space:pre-wrap}</style>'
    text+='<h1>真实专家选择下的局部短时故障</h1><p>8份不同R1-AWQ/MMLU请求；两个EP4组共享同一网络。第一组热点接收端在前100微秒丢弃数据，第二组没有直接丢包。原层3、4，两步Decode；计算和部署为声明参考。</p>'
    text+='<table><tr>'+''.join('<th>'+v+'</th>' for v in columns)+'</tr>'+''.join(table)+'</table>'
    text+='<h2>相对同机制正常场景的变化</h2>'
    for mode,c in contrasts.items():
        text+=f'<p>{mode.upper()}：整段服务增加{c["application_extra_ns"]/1000:.3f}微秒；未直接丢包组变化{c["group1_extra_ns"]/1000:+.3f}微秒。负值表示该组反而更早结束。</p>'
    text+='<p>这些差值不等于可优化收益。需进一步辨别排队、反馈、任务提交时间和恢复流量；SR还使用额外接收缓存及反馈扩展，不能称等内存对照。没有验证新算法或生产发生率。</p>'
    text+='<p><a href="analysis.json">机器摘要</a>；本目录CSV保存逐消息、收发端统计及两组首层Dispatch累计有效到达曲线。完整逐包原件保存在对应运行的result.json。</p></html>'
    (output/'report.html').write_text(text)
    print(json.dumps(dict(passed=True,cases=4,contrasts=contrasts),ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();analyze(a.root.resolve(),a.output.resolve())
