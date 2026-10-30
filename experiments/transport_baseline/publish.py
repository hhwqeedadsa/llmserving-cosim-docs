"""Publish the frozen study plus post-run packet/gate mechanism evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def load(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--study',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();e=load(a.study/'delivery/analysis.json')
    assert load(a.study.parent/'verification.json')['passed']
    evidence={}
    raw={name:load(a.study/name/'result.json') for name in e['cases']}
    # The normal packet and queue traces must actually match, beyond final time.
    normal=raw['base-normal'];short_normal=raw['short-normal']
    assert normal['packet_capture']==short_normal['packet_capture']
    assert normal['queue_events']==short_normal['queue_events']
    assert normal['service_transport']['native']['source_packets']==short_normal['service_transport']['native']['source_packets']
    for name in ('base-local','short-local'):
        r=raw[name];native=r['service_transport']['native']
        m=next(m for m in r['service_transport']['messages'] if m['src']==0 and m['dst']==32 and m['ready_ns']==1030)
        stream=m['stream'];assert m['begin']==0 and m['bytes']==14464
        cols=native['source_packet_columns']
        packets=[dict(zip(cols,row)) for row in native['source_packets'] if row[cols.index('stream')]==stream]
        assert [x['sequence'] for x in packets]==[0,9000,0,9000]
        assert sum(x['payload_bytes'] for x in packets)==2*m['bytes']
        cap=r['packet_capture'];cols=cap['columns']
        accepted=[dict(zip(cols,row)) for row in cap['events'] if row[cols.index('event')]==cap['event_codes']['rx']
                  and row[cols.index('node')]==32 and row[cols.index('ip_protocol')]==17
                  and row[cols.index('message_id')]==stream]
        assert len(accepted)==2 and [x['sequence'] for x in accepted]==[0,9000]
        assert all(x['time_ns']>=20000 for x in accepted)
        assert accepted[-1]['time_ns']==m['receive_ready_ns']
        timeout=e['cases'][name]['timeout_events'];assert len(timeout)==1 and timeout[0]['message_id']==stream
        assert timeout[0]['time_ns']==packets[2]['time_ns']
        gate=e['cases'][name]['gates_ns']['g0.w0.L0.dispatch']
        assert gate==m['ack_ns']
        call=next(c for c in load(a.study/('diagnosis-'+name)/'calls.json') if c['call']=='g0.w0.l0.r0.s0')
        tail=next(b for b in call['bundles'] if b['owner']==68)
        assert tail['input_ready_ns']==10146 and tail['gate_strictly_last_dependency']
        assert tail['expert_start_ns']==gate and tail['expert_fifo_wait_ns']==0
        history=[h for h in load(a.study/name/'frontend.json')['history'] if h['group_index']==0]
        assert history[1]['start_ns']==history[0]['finished_ns']
        evidence[name]=dict(stream=stream,message=m,source_transmissions=packets,actual_data_rx=accepted,
            timeout=timeout[0],first_dispatch_gate_ns=gate,
            unaffected_input_r68=dict(input_ns=tail['input_ready_ns'],expert_start_ns=tail['expert_start_ns'],fifo_wait_ns=0),
            target_step_history=history)
    # This explanatory publication is deliberately specific to this observed study.
    # Refuse to reuse its fixed narrative/table for a different measured outcome.
    expected={
        'base-normal':(121120,121120,0,1947456),
        'short-normal':(121120,121120,0,1947456),
        'base-local':(189208,120531,14464,1832640),
        'short-local':(125977,120531,14464,1904192)}
    for name,values in expected.items():
        row=e['cases'][name]
        assert (row['group_complete_ns']['0'],max(row['group_complete_ns'][k] for k in ('1','2','3')),
                row['retransmitted_payload_bytes'],row['buffer_peak_bytes'])==values
    assert e['calibration']['normal_max_ready_ack_ns']==16009 and e['calibration']['initial_rto_ns']==17000
    for name,timeout,received,ack in [('base-local',81030,83502,85504),('short-local',18030,20502,22504)]:
        row=evidence[name]
        assert row['timeout']['time_ns']==timeout and row['message']['receive_ready_ns']==received
        assert row['message']['ack_ns']==ack
        assert [x['time_ns'] for x in e['cases'][name]['packet_drops']]==[3392,4336]
        assert [x['time_ns'] for x in row['actual_data_rx']]==[received-110,received]
    b=e['cases']['base-local'];s=e['cases']['short-local'];n=e['cases']['base-normal']
    saved=e['effects']['local']['target_saved_ns'];penalty=e['base_fault_penalty_ns']
    facts=dict(passed=True,normal_packet_and_queue_traces_identical=True,cases=evidence,
        target_saved_fraction=saved/b['group_complete_ns']['0'],
        original_fault_penalty_ns=penalty,fault_penalty_recovered_fraction=saved/penalty,
        remaining_gap_to_normal_ns=s['group_complete_ns']['0']-n['group_complete_ns']['0'],
        extra_arena_peak_bytes=s['buffer_peak_bytes']-b['buffer_peak_bytes'],
        original_dispatch_advance_ns=e['effects']['local']['first_dispatch_saved_ns'],
        target_advance_ns=saved,publisher_sha256=sha(Path(__file__)),
        assertion_scope='These four fixed networks only; no optimal-RTO, production or novelty claim')
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    for source in (a.study/'delivery').iterdir():
        if source.is_file():shutil.copyfile(source,out/source.name)
    # Matplotlib emits trailing spaces in SVG path data; normalize formatting
    # only in the published copy, retaining the original generated figure.
    svg=out/'completion.svg'
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
    (out/'mechanism.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2)+'\n')
    section=f'''<section><h2>收益到底有多少，还剩多少空间？</h2>
<p>本次目标片段减少{saved/1000:.3f}微秒，相对原故障条件缩短{saved/b['group_complete_ns']['0']*100:.2f}%。原正常/故障差为{penalty/1000:.3f}微秒，成熟短超时收回其中{saved/penalty*100:.2f}%；与原正常条件还差{facts['remaining_gap_to_normal_ns']/1000:.3f}微秒。剩余差距只用于本场景参考，不能直接称为新算法的可消除上限。</p>
<p>实际代价：两种故障条件均只重传14,464字节、丢2包，其他三组完成时间相同。短超时条件消息arena峰值增加{facts['extra_arena_peak_bytes']:,}字节（1,832,640→1,904,192），因此不能称零开销。正常条件完整逐包及队列记录相同，没有多发数据。</p>
<p>因果链已经逐包核实：同一stream的两个原包在3.392/4.336微秒丢失。超时由81.030提前到18.030微秒；重传实际到达由83.392/83.502提前到20.392/20.502微秒，恰在20微秒故障结束之后。源确认及Dispatch门限由85.504提前到22.504微秒；输入早已到达的r68专家随门限开始，实际FIFO等待均为0。这说明本例主要卡在原始通信完成，而非缺少专家计算槽。</p>
<p>本次单故障窗口和稀疏负载有利于短超时重传成功；故障更久、排队更深或ACK受损时不能照搬收益。下一轮应在强基线下增加已有真实请求和故障窗口，检验误重传、重传竞争与健康任务影响，再判断按通信组进度分配恢复预算是否有额外价值。简单调低超时不作为创新。</p>
<p><a href="mechanism.json">逐包与门限核查证据</a></p></section>'''
    page=(out/'report.html').read_text()
    marker='<section><h2>这说明什么？</h2>'
    assert page.count(marker)==1
    (out/'report.html').write_text(page.replace(marker,section+'\n'+marker))
    (out/'README.md').write_text(f'''# 原始通信恢复的收益与代价

[网页](report.html)、[对比图](completion.png)、[主结果](analysis.json)、[逐包机制](mechanism.json)。方法先提交于`dced696e28`，四个新128端点Clos网络各执行一次，零构建和下载。平台行为无修改；采用公开prepare/run/check/contribution-frontier，四条件全部observe-only、没有备用执行。

正常214条消息最长ready→ACK跨度16,009ns，由事前规则取17µs初始RTO，与原80µs比较，最大640µs及3次重试不变。此消息跨度包含发布和排队，不是实测RTT、最优RTO或独立泛化验证。

| 条件 | 目标两步片段完成 | 其他三组最晚 | 重传载荷 | 消息arena峰值 |
|---|---:|---:|---:|---:|
| 原RTO / normal | 121.120µs | 121.120µs | 0B | 1,947,456B |
| 短RTO / normal | 121.120µs | 121.120µs | 0B | 1,947,456B |
| 原RTO / local | 189.208µs | 120.531µs | 14,464B | 1,832,640B |
| 短RTO / local | 125.977µs | 120.531µs | 14,464B | 1,904,192B |

目标减少63.231µs（33.42%），收回本次原正常/故障差68.088µs的92.87%，剩4.857µs差距不是可消除上限。正常逐包/队列完全相同；其他三组在策略间逐组完成时刻相同，不能由此外推所有负载无干扰。故障时消息arena峰值增加71,552B，不称零开销。

同一stream10的两个原包在GPU32丢失，只有一次超时重传。超时81.030→18.030µs、实际收到完整输入83.502→20.502µs、源确认及Dispatch门限85.504→22.504µs。r68输入均10.146µs到，专家均随Dispatch门限开始，实际FIFO等待0。门限提前63µs，片段提前63.231µs；二者不相加，其余231ns是后续执行时间的合计变化，未单独归因，完整逐步时刻留在证据，不归为额外算法贡献。

这项结果反驳以原80µs超时为唯一比较基线继续宣称大幅创新收益。短超时属于成熟恢复方法；后续需要在它之上增加请求并检验故障持续时间/排队变化。四份真实专家选择、两层两步、声明计算和标记字节，不能代表完整671B、全卡满载、硬件性能、p99或生产经济收益。网络保持全部原门限/确认及实际缓冲释放，四条件最终资源为0。

公开核验及诊断覆盖64个层步调用，独立检查消息/源包字节、接收/源确认与Dispatch因果链、原图一致性、正常完整包队列一致性和缓冲退出。PNG已人工查看，网页仅检查静态资源与链接，不声称浏览器渲染。资源、完整命令和原始数据留于`runs/transport_baseline_20261030`。
''')
    print(json.dumps(dict(published=True,output=str(out),saved_ns=saved,remaining_gap_ns=facts['remaining_gap_to_normal_ns'])))


if __name__=='__main__':
    main()
