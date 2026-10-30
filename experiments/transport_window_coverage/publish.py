"""Post-run explanation of arrival-time loss and the observed healthy-task cost."""
import argparse
from collections import defaultdict
import gc
import hashlib
import json
from pathlib import Path
import shutil


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def packet_path(events,uid,stream,protocol):
    rows=[x for x in events if x['packet_uid']==uid and x['message_id']==stream and
          x['ip_protocol']==protocol and x['event'] in (0,1,2)]
    assert rows and all(x['time_ns']<=y['time_ns'] for x,y in zip(rows,rows[1:]))
    switches={}
    for row in rows:
        if row['node']>=128:
            values=switches.setdefault(row['node'],{})
            if row['event']==1:assert 'rx_ns' not in values;values['rx_ns']=row['time_ns']
            if row['event']==0:
                assert 'tx_ns' not in values;values.update(tx_ns=row['time_ns'],port=row['port'])
    for x in switches.values():x['residence_ns']=x['tx_ns']-x['rx_ns'];assert x['residence_ns']>=0
    return dict(events=rows,switches=switches,source_tx_ns=rows[0]['time_ns'],
                destination_arrival_ns=rows[-1]['time_ns'],dropped=rows[-1]['event']==2)


def critical_send(study,name,r,graph,phase):
    gate=next(g for g in r['service']['phase_completion']['gates'] if g['graph_id']==graph and g['id']==phase)
    wave=load(study/name/'graphs'/graph/'wave.json');messages={m['id']:m for m in r['service_transport']['messages']}
    hits=[]
    for member in gate['members']:
        if member['arrived_ns']!=gate['released_ns']:continue
        nodes=wave['rank_nodes'][str(member['rank'])]
        gn=next(n for n in nodes if n['id']==member['node'])
        for node in nodes:
            if node['id'] in gn['deps'] and node['kind']=='SEND':
                m=messages[node['message']['id']]
                if m['ack_ns']==gate['released_ns']:hits.append(m)
    assert len(hits)==1,'this post-run explanation requires the observed unique SEND gate predecessor'
    return gate,hits[0]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();study=a.study
    e=load(study/'delivery/analysis.json');v=load(study.parent/'verification.json');assert v['passed']
    assert e['new_networks']==12 and v['calls_checked']==720
    root=Path(__file__).resolve().parents[2]
    for key,digest in e['source_sha256'].items():assert sha(root/key)==digest
    for key,digest in v['files_sha256'].items():assert sha(study/'delivery'/key)==digest
    mechanism={};normal_packets=normal_queues=None
    for name in ('base-normal','transfer-normal','calibrated-normal','base-loss60','transfer-loss60','calibrated-loss60','base-loss20','transfer-loss20'):
        r=load(study/name/'result.json');cap=r['packet_capture'];cols=cap['columns']
        events=[dict(zip(cols,row)) for row in cap['events']]
        if name.endswith('normal'):
            if normal_packets is None:normal_packets=cap;normal_queues=r['queue_events']
            assert cap==normal_packets and r['queue_events']==normal_queues
            continue
        if name.endswith('loss60'):
            row=e['cases'][name];damaged=[]
            for m in row['damaged_first_dispatch_messages']:
                assert m['begin']==0
                attempts=[]
                for packet in row['damaged_stream_source_packets']:
                    if packet['stream']!=m['stream'] or packet['sequence']>=m['end'] or packet['time_ns']>m['ack_ns']:continue
                    path=packet_path(events,packet['packet_uid'],m['stream'],17)
                    assert path['source_tx_ns']==packet['time_ns']
                    assert path['events'][0]['node']==m['src'] and path['events'][-1]['node']==32
                    assert path['dropped']==(path['destination_arrival_ns']<60000)
                    attempts.append(dict(source_packet=packet,path=path))
                assert attempts and max(x['path']['destination_arrival_ns'] for x in attempts if not x['path']['dropped'])==m['receive_ready_ns']
                damaged.append(dict(message=m,attempts=attempts))
            mechanism[name]=dict(damaged=damaged,first_dispatch_ns=row['first_dispatch_ns'],timeouts=row['timeouts'])
        if name.endswith('loss20'):
            # Chosen after inspection: group2 had the largest observed positive
            # matched healthy-group completion difference. This is not blind selection.
            gate,m=critical_send(study,name,r,'g2.w1','L0.combine')
            n=r['service_transport']['native'];cols=n['source_packet_columns']
            last=[dict(zip(cols,row)) for row in n['source_packets'] if row[cols.index('stream')]==m['stream'] and
                  row[cols.index('sequence')]+row[cols.index('payload_bytes')]==m['end'] and row[cols.index('time_ns')]<=m['receive_ready_ns']]
            assert len(last)==1
            data=packet_path(events,last[0]['packet_uid'],m['stream'],17)
            assert not data['dropped'] and data['destination_arrival_ns']==m['receive_ready_ns']
            ack=[x for x in events if x['event']==1 and x['ip_protocol']==252 and x['node']==m['src'] and
                 x['message_id']==m['stream'] and x['time_ns']==m['ack_ns'] and x['sequence']>=m['end']]
            assert len(ack)==1
            feedback=packet_path(events,ack[0]['packet_uid'],m['stream'],252)
            final_gate,final=critical_send(study,name,r,'g2.w1','L1.combine')
            switches=data['switches'];assert 133 in switches
            q=switches[133];streams={x['id']:x for x in n['streams']}
            topology=load(study/name/'topology.json')
            link=next(x for x in topology['links'] if (x['src'],x['src_port'])==(133,q['port']) or (x['dst'],x['dst_port'])==(133,q['port']))
            rate=link['rate_bps'];occupiers=[]
            for x in events:
                if x['node']!=133 or x['port']!=q['port'] or x['event']!=0 or not q['rx_ns']-1000<=x['time_ns']<q['tx_ns']:continue
                duration=(x['model_bytes']*8*10**9+rate-1)//rate
                occupied=max(0,min(q['tx_ns'],x['time_ns']+duration)-max(q['rx_ns'],x['time_ns']))
                if occupied:
                    stream=streams.get(x['message_id'])
                    occupiers.append(dict(packet=x,serialization_ns=duration,occupied_interval_ns=occupied,
                        src=None if stream is None else stream['src'],dst=None if stream is None else stream['dst'],
                        source_group=None if stream is None else stream['src']%4))
            assert sum(x['occupied_interval_ns'] for x in occupiers)==q['residence_ns']
            mechanism[name]=dict(critical_message=m,gate_ns=gate['released_ns'],last_payload_source=last[0],
                data_path=data,ack_path=feedback,final_message=final,final_gate_ns=final_gate['released_ns'],
                switch133_port=q['port'],switch133_residence_ns=q['residence_ns'],wire_occupiers=occupiers)
        del r,events;gc.collect()
    b=mechanism['base-loss20'];s=mechanism['transfer-loss20']
    assert b['critical_message']['id']==s['critical_message']['id']==25485
    assert b['final_message']['id']==s['final_message']['id']
    assert set(b['data_path']['switches'])==set(s['data_path']['switches'])
    deltas={str(node):s['data_path']['switches'][node]['residence_ns']-x['residence_ns']
            for node,x in b['data_path']['switches'].items()}
    start=s['data_path']['source_tx_ns']-b['data_path']['source_tx_ns']
    arrival=s['data_path']['destination_arrival_ns']-b['data_path']['destination_arrival_ns']
    assert arrival==start+sum(deltas.values())==459
    assert s['gate_ns']-b['gate_ns']==arrival
    assert s['ack_path']['destination_arrival_ns']-s['ack_path']['source_tx_ns']==b['ack_path']['destination_arrival_ns']-b['ack_path']['source_tx_ns']
    later=(s['final_message']['ack_ns']-s['final_message']['ready_ns'])-(b['final_message']['ack_ns']-b['final_message']['ready_ns'])
    final=s['final_gate_ns']-b['final_gate_ns']
    assert start==6 and sum(deltas.values())==453 and later==-21 and final==start+sum(deltas.values())+later==438
    assert sum(x['occupied_interval_ns'] for x in s['wire_occupiers'] if x['source_group']==0)==416
    facts=dict(passed=True,normal_packet_and_queue_traces_equal=True,cases=mechanism,
        healthy_cost=dict(selected_after_inspection='largest positive other-group difference: transfer-loss20 group2',
            source_tx_shift_ns=start,switch_residence_changes_ns=deltas,total_extra_residence_ns=sum(deltas.values()),
            first_combine_gate_shift_ns=arrival,later_critical_ready_to_ack_change_ns=later,completion_delay_ns=final,
            recovered_target_group_serialization_during_wait_ns=416,
            interpretation='6ns carried start shift + 453ns extra packet residence - 21ns later transfer span = 438ns; do not sum with phase or request delays'),
        publisher_sha256=sha(Path(__file__)),scope='Observed post-run explanation, no new networks or novel recovery policy')
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    for path in (study/'delivery').iterdir():
        if path.is_file():shutil.copyfile(path,out/path.name)
    (out/'mechanism.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2)+'\n')
    normal=e['cases']['base-normal'];table=''
    for w in ('loss20','loss60','loss120'):
        base=e['cases']['base-'+w];row=e['cases']['transfer-'+w]
        table+=f"| {w} | {base['group_complete_ns']['0']/1000:.3f}µs | {row['group_complete_ns']['0']/1000:.3f}µs | {row['retransmitted_payload_bytes']:,}B | {row['wasted_retry_payload_bytes']:,}B |\n"
    (out/'README.md').write_text('''# 多请求与故障窗口：本轮结果

[网页](report.html)、[对比图](comparison.png)、[结果](analysis.json)、[逐包与排队机制](mechanism.json)。平台输入扩展`367f23d345`，研究方法`c2de771406`在执行前提交。12个128端点Clos网络各执行一次；15份不同真实路由、四组EP32、两层两步、声明计算和标记字节，不是完整671B或128卡满载。

正常778条消息、有效载荷11,228,096B；最长ready→ACK跨度17,466ns，按冻结规则得到18µs，另保留原80µs及上一轮17µs。三个正常条件目标均129.537µs，完整逐包与队列记录相同、重传0。消息跨度不是RTT，也不能仅因17.466>17就断言17µs必然误超时；本实现计时器从实际发送开始，实际累计ACK进展可重置超时。

| 故障窗口 | 80µs目标完成 | 17µs目标完成 | 17µs重传 | 其中再次丢失 |
|---|---:|---:|---:|---:|
'''+table+'''
**短超时不是在所有窗口都更快。**60µs故障时17µs比80µs晚38.725µs（19.73%），多发57,856B。GPU32实际丢12包而非4包，多出的来自重传仍落在故障窗口。20µs窗口17µs早60.992µs；120µs窗口早121µs。18µs校准策略在60µs窗口也更慢，说明正常校准没有解决所有故障持续时间的退避问题。故障未来时长不能预先读取，按结果选最快只属于事后参考，不是已实现策略。

受损源0、源12各有一条14,464B的首Dispatch。80µs策略在81.030/81.935µs首次超时；17µs策略在18.030/18.935、52.030/52.935、120.030/120.935µs超时。60µs窗口前两轮仍丢，第三轮才到达；全部确认/门限仍由原消息推进，不靠备用或伪造ACK。

18µs的60µs窗口还展示了位置差异：源0第二轮55.030µs发出，首包57.392µs到GPU32仍丢；源12第二轮55.935µs发出，首包60.755µs到达并成功。前者经过两条物理链路，后者四条并有实际排队。故障按到达时刻判定；这不代表长路径普遍更好。

匹配20µs故障对照中，17µs策略使健康组2晚438ns。实际关键消息90→14在Agg133端口2的末包驻留由72ns增至597ns，其他端口驻留变化抵消72ns，合计增加453ns；源发送已有6ns差，后续关键传输跨度少21ns，最终6+453−21=438ns。该等待区间内恢复目标组0的实际发包占线416ns，全部物理TX记录保留。健康组2相对无故障正常条件仍更早，不能把这个策略间的微小差异说成故障使其净恶化或严重全网拥塞。

代价同时看消息峰值与排空：20µs窗口17µs峰值比80µs少28,736B；60µs窗口少1,412,672B，但完成更晚，不能把峰值下降单独当优化。三个正常目标129.537µs，最晚健康组135.773µs，物理排空151.868µs；不同完成与资源口径不能混用。

当前发现是成熟超时/指数退避的适用边界，不是新协议。下一步优先核对多个任务真正共用的上行/跨Pod故障与现成绕路恢复，保持这些较强基线，判断是否存在更有意义的资源竞争空间；现有15个单token Decode请求不能证明大batch或Prefill场景。原图、源ACK和缓冲退出均保留，720个层步调用、全部消息/重传字节及12份图/输入独立核验通过。PNG已查看，HTML仅静态资源检查，零构建/下载/硬件校准。
''')
    section='''<section><h2>本轮结论：短超时为什么有时反而更慢？</h2>
<p>正常目标均129.537微秒，三策略完整包与队列记录一致。20微秒故障时17微秒超时比80微秒早60.992微秒；60微秒故障时反而晚38.725微秒、多重传57,856字节；120微秒故障时早121微秒。正常校准得到的18微秒也没有消除60微秒窗口的退避代价。不能按事后已知故障时长选择最快结果并算作已实现策略。</p>
<p>60微秒窗口内，17微秒策略前两轮重传仍丢，第三轮才成功；80微秒策略第一轮就已避开窗口。18微秒策略中，源0的重传首包57.392微秒到达仍丢，源12因不同跳数与排队在60.755微秒到达成功。接收端按实际到达时刻丢包，发送时刻不能代替到达时刻。</p>
<p>匹配20微秒故障条件下，健康组2最多晚438纳秒：关键包增加453纳秒交换机驻留，带上已有6纳秒发送差，再减后续21纳秒传输跨度，最终传播到组完成。Agg133端口2可看到恢复目标组的实际包占线。这是小幅、可解释的竞争，不支持严重全网拥塞的结论。</p>
<p><a href="mechanism.json">逐包路径、端口占用和门限证据</a> · <a href="README.md">完整人话解释与研究边界</a></p></section>'''
    page=(out/'report.html').read_text();marker='<section><h2>如何判断研究空间？</h2>';assert page.count(marker)==1
    (out/'report.html').write_text(page.replace(marker,section+marker))
    print(json.dumps(dict(published=True,healthy_delay_ns=final,extra_residence_ns=sum(deltas.values()),output=str(out))))


if __name__=='__main__':main()
