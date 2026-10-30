"""Post-result audit of the actual interleaved recovery batch and gate slack."""
import argparse
import hashlib
import html
import json
from pathlib import Path


def load(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--delivery',type=Path,required=True)
    a=p.parse_args();f=load(a.stage/'input/frozen.json');analysis=load(a.stage/'analysis.json')
    cases={}
    for name in ('prefix-w20','prefix-w60'):
        reference=f['cases'][name];d=Path(reference['directory']);r=load(d/'result.json')
        assert sha(d/'result.json')==reference['result_sha256']
        chosen=[b for b in analysis['cases'][name]['batches'] if b['other_QP_occupied_ns']]
        rates={(p['node'],p['port']):p['rate_bps'] for p in r['ports']}
        witnesses=[];packets={}
        for b in chosen:
            sid=b['trigger']['stream'];ranges=b['missing_ranges']
            matches=[m for m in r['service_transport']['messages'] if m['stream']==sid and
                m['ready_ns']<=b['trigger']['time_ns']<m['ack_ns'] and
                any(max(m['begin'],left)<min(m['end'],right) for left,right in ranges)]
            assert len(matches)==1;m=matches[0]
            waves=[load(p) for p in (d/'graphs').glob('*/wave.json')]
            wave=next(w for w in waves if m['id'] in {x['id'] for x in w['messages']})
            gate=next(g for g in r['service']['phase_completion']['gates'] if
                (g['graph_id'],g['id'])==(wave['graph_id'],'L'+str(m['layer'])+'.'+m['phase']))
            assert gate['released_ns']==max(x['arrived_ns'] for x in gate['members'])
            assert m['ack_ns']<gate['released_ns']
            witnesses.append(dict(stream=sid,message=m,gate=gate,
                original_gate_slack_ns=gate['released_ns']-m['ack_ns'],
                competing_QP_occupied_ns=b['other_QP_occupied_ns']))
            for x in b['actual_retransmission_packets']:packets[x['packet_uid']]=x
        assert len(chosen)==2 and sum(b['other_QP_occupied_ns'] for b in chosen)==291
        cap=r['packet_capture'];phys=[]
        for row in cap['events']:
            x=dict(zip(cap['columns'],row))
            if x['packet_uid'] in packets and x['event']==cap['event_codes']['tx'] and x['node']==1:
                phys.append(x)
        assert len(phys)==len(packets)==4
        schedule=[]
        for x in sorted(packets.values(),key=lambda x:x['time_ns']):
            assert x['src']==1 and x['nic']==1
            frame=x['payload_bytes']+36
            duration=round(frame*8*10**9/rates[x['src'],x['nic']])
            schedule.append(dict(packet=x,model_bytes=frame,start_ns=x['time_ns'],end_ns=x['time_ns']+duration))
        assert all(a['end_ns']==b['start_ns'] for a,b in zip(schedule,schedule[1:]))
        assert schedule[0]['start_ns']==18754 and schedule[-1]['end_ns']==19336
        cases[name]=dict(result_sha256=reference['result_sha256'],messages=witnesses,actual_NIC_schedule=schedule,
            physical_source_TX=phys,scope='original gate slack and serialization; no counterfactual latency claim')
    value=dict(passed=True,post_result_diagnosis=True,cases=cases,source_sha256=sha(Path(__file__)))
    with (a.delivery/'competition.json').open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    table='\n'.join('| '+name+' | '+str(x['message']['id'])+' | '+str(x['competing_QP_occupied_ns'])+' | '+str(x['original_gate_slack_ns'])+' |'
        for name,c in cases.items() for x in c['messages'])
    text=f"""# 首包零等待，不等于整个重传批次没有竞争

本节为完成原定分析后对唯一批次交错的诊断。没有改变输入、计时或网络，只读原轨迹；来源及物理包见[competition.json](competition.json)。

两个prefix条件各有10次超时和2次NACK快速重传。12个触发的首包都同纳秒发出，全部源可知缺口都在下一次源反馈前重发完毕；没有同纳秒反馈顺序歧义。

但是源1的NIC1存在一处真实的交错：

| 时间区间ns | 连接 | 实际重传payload |
|---|---:|---:|
| 18754–18935 | stream65，1→65 | 前9,000B |
| 18935–19116 | stream69，1→81 | 前9,000B |
| 19116–19226 | stream65，1→65 | 后5,464B |
| 19226–19336 | stream69，1→81 | 后5,464B |

stream65批次本身需要291ns串行化，因另一连接占线181ns，总跨度472ns。stream69本身也需要291ns，期间另一连接占线110ns，总跨度401ns。它们都没有额外未解释的间隔。两条连接的首包立即发出，却仍可在批次中途互相排队，因此只统计首包会漏掉竞争。

再映射回实际MoE集合：

| 条件 | 消息 | 批次内其他QP占线ns | 在原集合门限前完成的余量ns |
|---|---:|---:|---:|
{table}

这些消息属于同一个g1.w0首Dispatch，在原轨迹中都不是最后完成项。它们的5.431–7.435µs余量明显大于这处0.110/0.181µs占线，但这不是调度收益的数学上界：改变顺序还可能改变下游反馈和别的任务。当前证据只足以降低“超时后源NIC优先级”在本场景的开发优先级，不能声称全部网络调度无价值。

创新筛选应先检查是否有持续共享竞争、是否影响真实集合门限、现有同信息基线是否能解决。需要构造更多真实请求和较大batch时，应先固定来源、输入规模和正常网络负载，再独立选择故障；不能按结果制造热点来维持方案收益。
"""
    (a.delivery/'competition_explanation.md').write_text(text)
    old='部分批次存在等待、竞争或不完整覆盖；应按原始记录进一步定位，不从首包时间直接下结论。'
    new='两个prefix条件各12次恢复均首包即发，全部缺口批次发齐；其中2个批次互占同一NIC累计291ns。首包零等待不能证明没有批次中途竞争。'
    for file in ('README.md','report.html'):
        path=a.delivery/file;body=path.read_text();assert old in body;body=body.replace(old,new)
        if file.endswith('.html'):
            body=body.replace('</html>','<h2>唯一恢复批次交错与集合余量</h2><p><a href="competition_explanation.md">完整解释</a> · <a href="competition.json">实际包与门限</a></p><pre>'+html.escape(text)+'</pre></html>')
        else:body+='\n\n[进一步核查：唯一批次交错与集合余量](competition_explanation.md)。\n'
        path.write_text(body)
    print(json.dumps(dict(passed=True,post_result_diagnosis=True,cases=list(cases),new_networks=0)))


if __name__=='__main__':main()
