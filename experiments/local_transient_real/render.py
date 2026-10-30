"""Export static research figures and a self-contained, portable result page."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def render(root,analysis,diagnosis,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    output.mkdir(parents=True,exist_ok=False)
    for p in analysis.iterdir():
        if p.is_file():shutil.copyfile(p,output/p.name)
    shutil.copyfile(diagnosis,output/'diagnosis.json')
    shutil.copyfile(root/'verification.json',output/'verification.json')
    prep=load(root/'prepared/preparation.json');record=load(analysis/'analysis.json');diag=load(diagnosis)
    cases=list(record['cases']);colors=['#2d5b95','#b05e36','#418b73','#9469ad']
    fig,axes=plt.subplots(2,2,figsize=(13,8),constrained_layout=True)
    for ax,group in zip(axes[0],(0,1)):
        values=[record['cases'][c]['groups'][group]['finished_ns']/1e6 for c in cases]
        ax.bar(cases,values,color=colors);ax.set_ylabel('Completion time (ms)');ax.set_title(f'Group {group} completion')
        ax.tick_params(axis='x',rotation=15)
        for i,v in enumerate(values):ax.text(i,v+0.025,f'{v:.3f}',ha='center',fontsize=9)
        ax.set_ylim(0,max(values)*1.15);ax.grid(axis='y',alpha=.2)
    for ax,group in zip(axes[1],(0,1)):
        for name,color in zip(cases,colors):
            with (analysis/(name+f'-group{group}-dispatch.csv')).open() as f:curve=list(csv.DictReader(f))
            # All rows retained in CSV; the figure only zooms the first layer.
            ax.step([float(r['time_ns'])/1000 for r in curve],[float(r['accepted_bytes'])/1024 for r in curve],
                where='post',label=name,color=color)
        limits=[record['cases'][name]['arrival_audit' if group==0 else 'other_slice'] for name in cases]
        ready=[r['slice']['data_ready_ns'] if 'slice' in r else r['data_ready_ns'] for r in limits]
        ax.set_xlim(0,max(ready)/1000*1.12);ax.set_xlabel('Simulation time (us)');ax.set_ylabel('Accepted useful data (KiB)')
        ax.set_title(f'Group {group}, first Decode / layer 3 Dispatch');ax.grid(alpha=.2);ax.legend(fontsize=8)
    for suffix in ('png','svg','pdf'):fig.savefig(output/('overview.'+suffix),dpi=180)
    plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(13,4),constrained_layout=True)
    for ax,group in zip(axes,(0,1)):
        for name,color in zip(cases,colors):
            with (analysis/(name+f'-group{group}-late-combine.csv')).open() as f:curve=list(csv.DictReader(f))
            ax.step([float(r['time_ns'])/1e6 for r in curve],[float(r['accepted_bytes'])/1024 for r in curve],
                where='post',label=name,color=color)
        ax.set_xlabel('Simulation time (ms)');ax.set_ylabel('Accepted useful data (KiB)')
        ax.set_title(f'Group {group}, second Decode / layer 4 Combine');ax.grid(alpha=.2);ax.legend(fontsize=8)
    for suffix in ('png','svg','pdf'):fig.savefig(output/('late-combine.'+suffix),dpi=180)
    plt.close(fig)
    for path in output.glob('*.svg'):
        path.write_text('\n'.join(line.rstrip() for line in path.read_text().splitlines())+'\n')
    page=(output/'report.html').read_text()
    text='<h2>逐包曲线与两个任务组</h2><img src="overview.svg" style="width:100%" alt="完成时间与累计有效到达曲线">'
    text+='<img src="late-combine.svg" style="width:100%" alt="后续Combine的累计有效到达曲线">'
    ready=[c['other_slice']['data_ready_ns'] for c in record['cases'].values()]
    text+=f'<p>未受损组第一步首层Dispatch完成时刻最大相差{(max(ready)-min(ready))/1000:.3f}微秒；最终组完成的变化发生在后续通信阶段。不能只看第一轮或单条流判断整段任务。</p>'
    text+='<h2>为什么没有直接丢包，结果仍会改变？</h2>'
    for name in ('gbn-local','sr-local'):
        c=diag['cases'][name]
        text+=f'<p>{name}在故障窗口的目标接收端为100%丢弃；整个网络全观察期则为{c["all_endpoint_data_drops"]}/{c["all_endpoint_data_arrival_attempts"]}个数据到达尝试（{100*c["all_endpoint_observed_drop_fraction"]:.3f}%），重传也计入分母。局部窗口比例与全网比例不可混用。</p>'
    if all(c['switch_drop_counts']==[0]*4 and c['pfc_pause']==0 for c in record['cases'].values()):
        text+='<p>四组都没有交换机自然丢弃或PFC暂停。</p>'
    text+='<p>超时恢复请求分别为'+ '、'.join(name+': '+str(c['recovery_event_counts']['timeout_retransmit'])
        for name,c in record['cases'].items())+'；这些请求数不能当作实际重传包数。</p>'
    same=diag['local_group1_same_source_sequence_size_multiset']
    difference=record['cases']['gbn-local']['groups'][1]['finished_ns']-record['cases']['sr-local']['groups'][1]['finished_ns']
    text+=f'<p>两个故障条件中，未直接丢包组的实际源发送序号/长度多重集{"完全相同" if same else "存在差异"}；GBN组完成时刻减去SR为{difference/1000:+.3f}微秒。</p>'
    for mode in ('gbn','sr'):
        tail=next(t for t in diag['cases'][mode+'-local']['tails'] if t['group']==1)
        text+=f'<p>{mode.upper()}未直接丢包组尾部消息：连接{tail["stream"]}，{tail["logical_bytes"]:,}B；历史最低参考速率{tail["min_historical_reference_rate_bps"]/1e6:.3f}Mb/s，实际发包跨度{tail["actual_sending_span_ns"]/1000:.3f}微秒。</p>'
    text+='<p>以上是当前参考模型中的限速/时序差异证据，尚未把全部差值因果分解为排队或反馈的独立贡献。</p>'
    tail=next(t for t in diag['cases']['sr-local']['tails'] if t['group']==0)
    text+=f'<p>SR受损组最后一条消息准备好后，{tail["first_send_wait_ns"]/1e6:.3f}毫秒才首次发送。发布时，该连接上前面的工作尚有{tail["earlier_unsent_bytes_at_publication"]:,}B未发送，不能把这段时间全部归为当前消息在交换机中排队。持久连接保留的拥塞状态和旧工作积压，需要一起分析。</p>'
    text+='<h2>收益与下一步判断</h2>'
    for mode in ('gbn','sr'):
        normal=record['cases'][mode+'-normal']['timing']['application_finished_ns']
        local=record['cases'][mode+'-local']['timing']['application_finished_ns']
        text+=f'<p>{mode.upper()}局部故障使整段服务相对正常条件变化{100*(local-normal)/normal:+.3f}%。</p>'
    baseline=record['cases']['gbn-local']['timing']['application_finished_ns']
    sr=record['cases']['sr-local']['timing']['application_finished_ns']
    text+=f'<p>SR故障条件相对GBN故障条件的服务时间降低{100*(baseline-sr)/baseline:.3f}%。这是成熟基线的实测差值，不是本项目新算法收益，也不是可消除空间上限。</p>'
    if record['contrasts']['sr']['group1_extra_ns']<0:
        text+='<p>未受损组在SR下反而比正常更快，否定了“更多重传必然让所有任务更慢”的简单解释。</p>'
    text+='<p>先核对目标通信库是否允许Combine在本rank的Dispatch尚未完成时就绪：当前图允许这种重叠，真实路径可能有flush或计算流屏障。若重叠不合法，应修正图并关闭依赖该重叠的优化候选。再验证阶段/通道分离连接等已有方法的资源成本、参考拥塞控制与目标硬件的一致性，以及独立路由窗口/共享上行故障下的结果。不能靠调大故障或削弱基线维持创新空间。</p><p>当前前端按每个专家贡献复制激活，未实现现代DeepEP按目的rank去重、完整GPU通知/可见性或生产RoCE报文。EP4只用于有限通信段，不证明完整R1权重可部署；每个条件执行一次，不证明统计泛化。</p>'
    text+='<p><a href="diagnosis.json">尾部消息及反馈诊断</a> · <a href="verification.json">独立核验与五类篡改拒绝</a> · <a href="overview.png">PNG图</a> · <a href="overview.pdf">PDF图</a></p>'
    page=page.replace('</html>',text+'</html>');(output/'report.html').write_text(page)
    summary=dict(schema_version='moe-local-transient-result-receipt/1',source_preparation_sha256=sha(root/'prepared/preparation.json'),
        execution_commits={name:load(root/'attempts'/(name+'.json'))['execution_commit'] for name in cases},
        sources=prep['sources'],groups=prep['groups'],logical_bytes=prep['logical_bytes'],messages=prep['messages'],
        stream_max_bytes=max(prep['planned_stream_bytes'].values()),sr_window_bytes=prep['sr_window_bytes'],
        sr_all_stream_receive_capacity=prep['sr_all_stream_capacity'],
        result_hashes={name:sha(root/name/'result.json') for name in cases},
        complete_captures=sum(c['packet_events'] for c in record['cases'].values()),
        remote_revision_verified=False,hardware_calibrated=False,new_algorithm_gain_demonstrated=False,
        files={p.name:dict(bytes=p.stat().st_size,sha256=sha(p)) for p in output.iterdir() if p.is_file()})
    (output/'receipt.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(passed=True,files=len(list(output.iterdir())),packet_events=summary['complete_captures'])))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('root','analysis','diagnosis','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();render(a.root.resolve(),a.analysis.resolve(),a.diagnosis.resolve(),a.output.resolve())
