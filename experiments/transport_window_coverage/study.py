"""Public-command study of source-local SR timers across fixed loss windows."""
import argparse
from collections import defaultdict
from copy import deepcopy
import gc
import hashlib
import html
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
POLICIES=('base','transfer','calibrated')
WINDOWS=dict(normal=0,loss20=20000,loss60=60000,loss120=120000)
CASES=tuple(p+'-'+w for p in POLICIES for w in WINDOWS)


def load(path):
    return json.loads(Path(path).read_bytes())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path,value):
    with Path(path).open('x') as s:
        json.dump(value,s,ensure_ascii=False,indent=2);s.write('\n')


def files(path):
    return {str(p.relative_to(path)):sha(p) for p in sorted(path.rglob('*')) if p.is_file()}


def cli(*args):
    return [sys.executable,'-B','-m','moe_ub_cosim','scaleout',*map(str,args)]


def public(command,receipt):
    save(receipt,dict(command=command,cwd=str(ROOT),status='started'))
    with receipt.with_suffix('.stdout.log').open('x') as out,receipt.with_suffix('.stderr.log').open('x') as err:
        result=subprocess.run(command,cwd=ROOT,stdout=out,stderr=err)
    record=load(receipt);record.update(exit_code=result.returncode,status='completed' if result.returncode==0 else 'failed')
    receipt.write_text(json.dumps(record,indent=2)+'\n')
    if result.returncode:raise SystemExit(f'Command failed; retained: {receipt}')


def prepare(a):
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    build=a.build.resolve();provenance=load(build/'provenance.json')
    for path,digest in provenance['sources'].items():assert sha(ROOT/path)==digest,path
    backend=build/'output/moe-rdma/ns3.42-moe-rdma-service'
    libraries=sorted((build/'output/lib').glob('libns3*.so'));assert len(libraries)==8
    prepared=out/'input'
    public([sys.executable,'-B',str(ROOT/'examples/scaleout_message_buffers/prepare.py'),
        '--source-manifest',str(a.source_manifest.resolve()),'--source-base',str(a.source_base.resolve()),
        '--route-files','15','--output',str(prepared)],out/'prepare-command.json')
    c=load(prepared/'service-buffers.json');c['inflight_helpers']['policy']='observe_only/1'
    assert len(c['route_replay']['requests'])==15 and [q['rank_id'] for q in c['route_replay']['requests']]==list(range(15))
    assert list(map(len,c['parallel']['groups']))==[32]*4
    assert c['transport']['transport_recovery']['initial_rto_ns']==80000
    save(prepared/'service-base.json',c)
    transfer=deepcopy(c);transfer['transport']['transport_recovery']['initial_rto_ns']=17000
    save(prepared/'service-transfer.json',transfer)
    p=load(prepared/'rdma-buffers.json');generator_target=p.pop('data_loss')['nodes']
    for name,end in WINDOWS.items():
        protocol=deepcopy(p)
        if end:protocol['data_loss']=dict(kind='periodic_destination_data/1',numerator=1,denominator=1,
            offset=0,start_ns=0,end_ns=end,nodes=[32])
        save(prepared/('rdma-'+name+'.json'),protocol)
    manifest=load(prepared/'buffer-source-manifest.json')
    routes={row['file']['path']:row['file']['sha256'] for row in manifest['files']}
    assert len(routes)==len(set(routes.values()))==15 and all(sha(p)==h for p,h in routes.items())
    sources=[*sorted((ROOT/'python/moe_ub_cosim').glob('*.py')),
             *sorted((ROOT/'examples').glob('*/prepare.py')),*sorted(Path(__file__).parent.glob('*.py')),
             Path(__file__).with_name('README.md')]
    save(out/'frozen.json',dict(schema_version='moe-transport-window-study/1',network_executed=False,
        platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        prepared=str(prepared),prepared_sha256=files(prepared),backend=str(backend),backend_sha256=sha(backend),
        compiled_sources_sha256=provenance['sources'],libraries_sha256={str(p):sha(p) for p in libraries},
        routes_sha256=routes,source_sha256={str(p.relative_to(ROOT)):sha(p) for p in sources},
        source_kind=manifest['source']['kind'],source_dataset_revision=manifest['source']['dataset_revision'],
        cases=list(CASES),windows_ns=WINDOWS,previous_fault_gpu=32,generator_suggested_fault_gpu=generator_target,
        max_new_networks=12,budget=dict(monitored_seconds=2400,artifact_bytes=2*2**30,min_free_bytes=8*2**30)))
    print(json.dumps(dict(prepared=True,requests=15,group_requests=[4,4,4,3],network_executed=False,
                         fault_gpu=32,generator_suggested_fault_gpu=generator_target)))


def checked(study):
    f=load(study/'frozen.json');assert sha(f['backend'])==f['backend_sha256']
    for group in ('source_sha256','compiled_sources_sha256'):
        for p,h in f[group].items():assert sha(ROOT/p)==h,p
    for group in ('routes_sha256','libraries_sha256'):
        for p,h in f[group].items():assert sha(p)==h,p
    current=files(Path(f['prepared']));current.pop('service-calibrated.json',None)
    assert current==f['prepared_sha256']
    return f


def calibrate(a):
    study=a.study.resolve();f=checked(study)
    assert not any((study/name).exists() for name in CASES if name!='base-normal')
    path=study/'base-normal';public(cli('rdma-check','--run',path),study/'calibration-check.json')
    r=load(path/'result.json');assert r['passed'] and r['drained'] and r['fault_drops']==0
    assert not r['service']['inflight_helpers']['attempts']
    messages=r['service_transport']['messages'];longest=max(m['ack_ns']-m['ready_ns'] for m in messages)
    timeout=(longest//1000+1)*1000
    c=load(Path(f['prepared'])/'service-base.json')
    assert 0<timeout<=c['transport']['transport_recovery']['max_rto_ns']
    c['transport']['transport_recovery']['initial_rto_ns']=timeout
    target=Path(f['prepared'])/'service-calibrated.json';save(target,c)
    values=dict(base=80000,transfer=17000,calibrated=timeout);canonical={};mapping={}
    for policy,value in values.items():
        equivalent=canonical.setdefault(value,policy)
        for window in WINDOWS:mapping[policy+'-'+window]=equivalent+'-'+window
    save(study/'calibration.json',dict(rule='next_strict_1000ns_above_all_normal_ready_to_ack_max/1',
        normal_result_sha256=sha(path/'result.json'),normal_max_ready_ack_ns=longest,initial_rto_ns=values,
        message_intervals={str(m['id']):dict(ready_ns=m['ready_ns'],ack_ns=m['ack_ns']) for m in messages},
        calibrated_config_sha256=sha(target),case_map=mapping,unique_networks=len(set(mapping.values())),
        normal_samples=1,independent_holdout=False,fault_used_for_calibration=False))
    print(json.dumps(dict(calibrated=True,messages=len(messages),max_ready_ack_ns=longest,RTO_ns=values,
                         unique_networks=len(set(mapping.values())))))


def run(a):
    study=a.study.resolve();f=checked(study);name=a.case
    if name!='base-normal':
        cal=load(study/'calibration.json')
        assert sha(study/'base-normal/result.json')==cal['normal_result_sha256']
        assert sha(Path(f['prepared'])/'service-calibrated.json')==cal['calibrated_config_sha256']
        canonical=cal['case_map'][name]
        if canonical!=name:
            r=load(study/canonical/'result.json');assert r['passed'] and r['drained']
            save(study/(name+'-alias.json'),dict(reused_from=canonical,result_sha256=sha(study/canonical/'result.json'),new_network=False))
            print(json.dumps(dict(case=name,reused_from=canonical,new_network=False)));return
    policy,window=name.split('-');out=study/name;assert not out.exists()
    p=Path(f['prepared'])
    public(cli('rdma-run','--config',p/('service-'+policy+'.json'),'--protocol',p/('rdma-'+window+'.json'),
        '--topology',p/'topology','--backend',f['backend'],'--protoc',a.protoc.resolve(),'--output',out),
        study/(name+'-command.json'))
    print(json.dumps(dict(case=name,completed=True,result_sha256=sha(out/'result.json'))))


def summary(path,diagnosis):
    r=load(path/'result.json');n=r['service_transport']['native']
    assert r['passed'] and r['drained'] and r['gpu_count']==128 and r['switch_count']==20
    assert n['physical_drained'] and n['closed'] and n['inflight_packets']==0
    assert r['service_transport']['message_buffers']['used_bytes']==n['message_payload']['retained_bytes']==0
    assert n['recovery']['complete'] and not n['recovery']['failed']
    assert r['packet_capture']['complete'] and r['packet_capture']['omitted_events']==0
    assert not r['service']['inflight_helpers']['attempts']
    columns=n['source_packet_columns'];packets=[dict(zip(columns,row)) for row in n['source_packets']]
    high=defaultdict(int);overlap={};tx=0;repeat=0
    for p in packets:
        assert p['sequence']<=high[p['stream']], 'source packet leaves an unexplained byte gap'
        end=p['sequence']+p['payload_bytes'];extra=max(0,min(end,high[p['stream']])-p['sequence'])
        overlap[p['packet_uid']]=extra;repeat+=extra;tx+=p['payload_bytes']
        high[p['stream']]=max(high[p['stream']],end)
    logical=sum(m['bytes'] for m in r['service_transport']['messages'])
    assert sum(high.values())==logical and tx-logical==repeat
    assert tx==sum(s['source_payload_bytes'] for s in n['streams'])
    cap=r['packet_capture'];cols=cap['columns']
    drops=[dict(zip(cols,row)) for row in cap['events'] if row[cols.index('event')]==cap['event_codes']['drop']]
    assert len(drops)==r['fault_drops']
    wasted=sum(overlap[d['packet_uid']] for d in drops)
    codes=n['recovery']['event_codes'];cols=n['recovery']['columns']
    timeouts=[dict(zip(cols,row)) for row in n['recovery']['events'] if row[cols.index('event')]==codes['timeout_retransmit']]
    history=load(path/'frontend.json')['history']
    groups={str(i):max(h['finished_ns'] for h in history if h['group_index']==i) for i in range(4)}
    gates={g['graph_id']+'.'+g['id']:g['released_ns'] for g in r['service']['phase_completion']['gates']}
    calls=load(diagnosis/'calls.json');first=next(c for c in calls if c['call']=='g0.w0.l0.r0.s0')
    representative={k:v for k,v in first.items() if k!='bundles'}
    representative['bundles']=[{k:v for k,v in b.items() if k!='experts'} for b in first['bundles']]
    original_messages=[m for m in r['service_transport']['messages'] if m['dst']==32 and m['phase']=='dispatch'
                       and m['layer']==0 and m['ready_ns']<20000]
    damaged_streams={m['stream'] for m in original_messages}
    return dict(result_sha256=sha(path/'result.json'),group_complete_ns=groups,drain_ns=r['finish_ns'],
        logical_bytes=logical,tx_payload_bytes=tx,retransmitted_payload_bytes=repeat,wasted_retry_payload_bytes=wasted,
        fault_drops=r['fault_drops'],drops=drops,timeouts=timeouts,
        initial_rto_ns=n['recovery']['config']['initial_rto_ns'],message_count=len(r['service_transport']['messages']),
        buffer_peak_bytes=r['service_transport']['message_buffers']['peak_bytes'],final_retained_bytes=0,
        first_dispatch_ns=gates['g0.w0.L0.dispatch'],first_target_call=representative,
        damaged_first_dispatch_messages=original_messages,
        damaged_stream_source_packets=[p for p in packets if p['stream'] in damaged_streams],
        diagnosis=load(diagnosis/'summary.json'))


def diagnose(a):
    study=a.study.resolve();checked(study);cal=load(study/'calibration.json')
    canonical=cal['case_map'][a.case]
    if canonical!=a.case:
        assert (study/('diagnosis-'+canonical)/'summary.json').is_file()
        print(json.dumps(dict(case=a.case,reused_diagnosis=canonical)));return
    public(cli('contribution-frontier','--run',study/canonical,'--output',study/('diagnosis-'+canonical)),
           study/(canonical+'-diagnosis.json'))
    print(json.dumps(dict(case=canonical,diagnosed=True)))


def analyze(a):
    study=a.study.resolve();f=checked(study);cal=load(study/'calibration.json')
    out=study/'delivery';out.mkdir(exist_ok=False)
    unique={};baseline_graphs=None;baseline_config=None
    for name in dict.fromkeys(cal['case_map'].values()):
        directory=study/name
        assert load(study/(name+'-diagnosis.json'))['exit_code']==0
        unique[name]=summary(directory,study/('diagnosis-'+name))
        graph_hashes=files(directory/'graphs')
        if baseline_graphs is None:baseline_graphs=graph_hashes
        assert graph_hashes==baseline_graphs,'original generated graphs differ'
        c=load(directory/'input.json');c['transport']['transport_recovery']['initial_rto_ns']=80000
        if baseline_config is None:baseline_config=c
        assert c==baseline_config,'more than initial RTO changed'
        assert sha(directory/'topology.json')==sha(Path(f['prepared'])/'topology/topology.json')
        gc.collect()
    rows={name:dict(unique[canonical],reused_from=canonical if canonical!=name else None)
          for name,canonical in cal['case_map'].items()}
    effects={}
    for window in WINDOWS:
        base=rows['base-'+window]
        for policy in ('transfer','calibrated'):
            row=rows[policy+'-'+window]
            effects[policy+'-'+window]=dict(target_saved_ns=base['group_complete_ns']['0']-row['group_complete_ns']['0'],
                other_group_delay_ns={str(i):row['group_complete_ns'][str(i)]-base['group_complete_ns'][str(i)] for i in (1,2,3)},
                extra_payload_bytes=row['tx_payload_bytes']-base['tx_payload_bytes'],
                extra_buffer_peak_bytes=row['buffer_peak_bytes']-base['buffer_peak_bytes'],
                dispatch_saved_ns=base['first_dispatch_ns']-row['first_dispatch_ns'])
    e=dict(schema_version='moe-transport-window-results/1',cases=rows,effects=effects,
        calibration=cal,new_networks=len(unique),builds=0,requests=15,source_kind=f['source_kind'],
        source_dataset_revision=f['source_dataset_revision'],same_original_graphs=True,graph_files_per_case=len(baseline_graphs),
        same_config_except_initial_rto=True,hardware_calibrated=False,complete_model=False,
        frozen_sha256=sha(study/'frozen.json'),source_sha256=f['source_sha256'])
    save(out/'analysis.json',e);render(out,e)
    print(json.dumps(dict(completed=True,networks=len(unique),RTO_ns=cal['initial_rto_ns'],effects=effects)))


def render(out,e):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(13,4.8))
    colors=('#9ba9b8','#2472a8','#2a8a69')
    for i,(policy,color) in enumerate(zip(POLICIES,colors)):
        data=[e['cases'][policy+'-'+w] for w in WINDOWS];xs=[j+(i-1)*.25 for j in range(4)]
        label=f'{policy}: {e["calibration"]["initial_rto_ns"][policy]/1000:g} us'
        axes[0].bar(xs,[r['group_complete_ns']['0']/1000 for r in data],width=.24,color=color,label=label)
        axes[1].bar(xs,[r['retransmitted_payload_bytes']/1024 for r in data],width=.24,color=color,label=label)
    for ax,title,ylabel in zip(axes,['Target group completion','Actual source retransmitted payload'],['Two-step fragment (us)','Payload (KiB)']):
        ax.set_xticks(range(4),['normal','20us loss','60us loss','120us loss']);ax.set_title(title);ax.set_ylabel(ylabel)
        ax.legend(fontsize=8);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    fig.suptitle('128-endpoint Clos, 15 distinct R1 route sources, fixed loss destination')
    fig.tight_layout()
    for ext in ('png','svg'):fig.savefig(out/('comparison.'+ext),dpi=170,bbox_inches='tight')
    plt.close(fig);svg=out/'comparison.svg';svg.write_text('\n'.join(x.rstrip() for x in svg.read_text().splitlines())+'\n')
    table=''.join('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in
        [name,r['initial_rto_ns']/1000,r['group_complete_ns']['0']/1000,
         max(r['group_complete_ns'][str(i)] for i in (1,2,3))/1000,len(r['timeouts']),r['fault_drops'],
         r['retransmitted_payload_bytes'],r['wasted_retry_payload_bytes'],r['buffer_peak_bytes'],r['reused_from'] or '独立执行'])+'</tr>'
        for name,r in e['cases'].items())
    (out/'report.html').write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>真实请求增加后，短超时还有效吗？</title><style>body{font:16px/1.8 system-ui;max-width:1250px;margin:30px auto;padding:0 20px;background:#f4f7fb;color:#183149}section{background:white;padding:22px;margin:20px 0;border-radius:12px}h1{font-size:28px}table{border-collapse:collapse;display:block;overflow:auto;font-size:14px}td,th{padding:8px;border:1px solid #d9e3ed;white-space:nowrap;text-align:left}img{width:100%}a{color:#1261a2}</style>
<h1>请求增加、故障更久时，成熟恢复基线表现如何？</h1>
<p>128端点Clos、四组EP32、15份不同R1-AWQ/MMLU专家选择（各组4/4/4/3请求）、两层两个Decode步。声明计算和标记字节，不是完整模型或硬件测量。所有策略均未执行备用。</p>
<section><h2>方法</h2><p>80微秒原超时、沿用的17微秒、由十五请求正常样本单独校准的超时；只改变初始RTO，最大640微秒、3次重试和全部原始任务门限保持。正常条件不注入故障；其他条件在前20/60/120微秒丢弃GPU32的DATA，含重传，控制包保留。故障位置沿用上一轮，不按新负载增强热点。窗口与校准规则在运行前固定，无独立验证集。</p></section>
<section><h2>实际完成与重传代价</h2><img src="comparison.png" alt="固定故障窗口下三个成熟超时策略的目标完成和重传载荷">
<table><tr><th>条件</th><th>RTOµs</th><th>目标完成µs</th><th>其他组最晚µs</th><th>超时</th><th>故障丢包</th><th>重传B</th><th>重传再次丢失B</th><th>缓冲峰值B</th><th>结果来源</th></tr>'''+table+'''</table>
<p>重传载荷由全部实际源包与完整有效载荷之差核算；“重传再次丢失”只计算已经发送过的字节在故障端再次丢弃的部分，不把原包丢失算进去。不同EP组的完成时延不相加；别名策略不是独立样本。</p></section>
<section><h2>如何判断研究空间？</h2><p>应先检查成熟超时及指数退避是否解释了变化，再判断是否需要新的恢复预算或调度。若固定短超时在某些窗口更慢，那是适用边界，不自动证明新机制必要；若新负载正常条件出现重传，还需要和正常校准策略对照。原始包、源确认、阶段门限和实际专家开始在派生证据中保留，不能从单个专家结果提前推断请求加速。</p>
<p>这些固定窗口是机制压力测试，没有生产频率或p99证据。17微秒或校准值都不是生产参数建议；真实专家选择也不代表真实激活字节或物理GPU性能。所有完整执行仍保留原ACK和资源退出义务。</p></section>
<section><h2>证据</h2><p><a href="analysis.json">全部派生数据与逐包时序</a> · <a href="comparison.svg">矢量图</a></p><p>公开核验与贡献诊断、原图/输入一致性和独立包字节核算完成后才能作为交付；网页仅做静态资源检查，不声称浏览器渲染。</p></section></html>''')


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    q=sub.add_parser('prepare')
    for name in ('source-manifest','source-base','build','output'):q.add_argument('--'+name,type=Path,required=True)
    for action in ('run','calibrate','diagnose','analyze'):
        q=sub.add_parser(action);q.add_argument('--study',type=Path,required=True)
        if action in ('run','diagnose'):q.add_argument('--case',choices=CASES,required=True)
        if action=='run':q.add_argument('--protoc',type=Path,required=True)
    a=p.parse_args();globals()[a.action](a)


if __name__=='__main__':main()
