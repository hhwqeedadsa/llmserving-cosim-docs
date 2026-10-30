"""Audit saved public runs and reject corrupted byte/range histories; no network."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.message_buffers import audit

ROOT=Path(__file__).resolve().parents[2]


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,value):
    with p.open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')


def scopes_for(directory,result):
    # Use native graph retirement and actual helper observations, not buffer rows.
    scopes={}
    for w in result['service']['waves']:
        wave=load(directory/'graphs'/w['graph_id']/'wave.json')
        for m in wave['messages']:
            if m['id'] in scopes:raise ValueError('duplicate original scope')
            scopes[m['id']]=w['finished_ns']
    helper_retired={}
    for line in (directory/'helper_events.jsonl').read_text().splitlines():
        e=json.loads(line)
        if e['kind']=='retired':
            if e['id'] in helper_retired:raise ValueError('duplicate helper retirement')
            helper_retired[e['id']]=e['time_ns']
    for attempt in result['service']['inflight_helpers']['attempts']:
        for key in ('input_message','result_message'):
            scopes[attempt['candidate'][key]]=helper_retired[attempt['id']]
    return scopes


def corrupt(result,kind):
    r=deepcopy(result);a=r['service_transport'];b=a['message_buffers'];n=a['native']
    if kind=='source_payload':n['source_payload_packets'][0]['hex']='ff'+n['source_payload_packets'][0]['hex'][2:]
    if kind=='receive_payload':
        event=next(e for e in n['selective_repeat']['events'] if e['kind']=='payload_delivered')
        event['hex']='ff'+event['hex'][2:]
    if kind=='source_alias_target':
        # Opposite-direction sources and targets share the same arena namespace.
        source=next(e for e in b['events'] if e['kind']=='source_acquired')
        target=next(e for e in b['events'] if e['kind']=='target_acquired' and e['owner']==source['owner'] and
                    next(m for m in b['messages'] if m['id']==source['message'])['ack_ns']>e['time_ns'])
        row=next(m for m in b['messages'] if m['id']==target['message'])
        row['target_address']=source['address']
        for e in b['events']:
            if e['message']==row['id'] and e['kind'] in ('target_acquired','target_write','target_ready','scope_finished','target_released'):
                e['address']=source['address']
    if kind=='early_release':b['messages'][0]['target_released_ns']-=1
    if kind=='fake_scope':b['messages'][0]['scope_finished_ns']-=1
    if kind=='duplicate_write':
        index=next(i for i,e in enumerate(b['events']) if e['kind']=='target_write')
        b['events'].insert(index,deepcopy(b['events'][index]))
    if kind=='wrong_digest':next(e for e in b['events'] if e['kind']=='target_write')['digest_fnv64']='0'*16
    if kind=='wrong_occupancy':b['events'][0]['global_used_bytes']-=1
    if kind=='missing_release':b['events'].pop()
    if kind=='retained_copy':n['message_payload']['retained_bytes']=1
    return r


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--synthetic-run',type=Path,required=True);p.add_argument('--real-run',type=Path,required=True)
    p.add_argument('--legacy-run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    legacy=args.legacy_run.resolve()
    before={str(f.relative_to(legacy)):sha(f) for f in legacy.rglob('*') if f.is_file()}
    cmd=[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-check','--run',str(legacy)]
    run=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True)
    (args.output/'legacy-check.stdout.log').write_text(run.stdout)
    (args.output/'legacy-check.stderr.log').write_text(run.stderr)
    assert run.returncode==0
    after={str(f.relative_to(legacy)):sha(f) for f in legacy.rglob('*') if f.is_file()}
    assert before==after
    summary=dict(schema_version='moe-message-buffer-acceptance/1',created_utc=datetime.now(timezone.utc).isoformat(),
        legacy_read_only=dict(public_check_exit=run.returncode,files=len(before),unchanged=True,
            scope='existing default-mode run read-only audit; not a new default-mode network'),cases={},
        limitations=['marker bytes, not activations or numerical experts','functional arena offsets, not CUDA/MR/rkey',
            'original graph consumption gates retained; no application recovery gain',
            'four real source requests are sparse, not full-cluster load'])
    for name,directory in [('synthetic',args.synthetic_run.resolve()),('real_routes',args.real_run.resolve())]:
        result=load(directory/'result.json');config=load(directory/'input.json');native=result['service_transport']['native']
        scopes=scopes_for(directory,result);verdict=audit(result,config['transport'],scopes)
        rejected={}
        for kind in ('source_payload','receive_payload','source_alias_target','early_release','fake_scope','duplicate_write',
                     'wrong_digest','wrong_occupancy','missing_release','retained_copy'):
            try:audit(corrupt(result,kind),config['transport'],scopes)
            except ValueError as error:rejected[kind]=str(error)
            else:raise AssertionError('corrupted history accepted: '+kind)
        assert result['passed'] and result['drained'] and result['gpu_count']==128
        attempt=result['service']['inflight_helpers']['attempts'][0]['candidate']
        rows={m['id']:m for m in result['service_transport']['message_buffers']['messages']}
        timing={key:rows[attempt[key]] for key in ('dispatch_message','combine_message','input_message','result_message')}
        case=dict(passed=True,network_runs=1,simulated_endpoints=128,ep_sizes=[len(g) for g in config['parallel']['groups']],
            requests=len(config['route_replay']['requests']),model=config['model'],source_layers=config['route_replay']['layer_ids'],
            decode_steps=config['workload']['decode_steps'],fault=result['data_loss'],
            helpers_retired=result['service']['inflight_helpers']['retired'],finish_ns=result['finish_ns'],
            packet_events=len(result['packet_capture']['events']),queue_events=len(result['queue_events']),
            verification=verdict,corruption_rejections=rejected,example_buffer_lifetimes=timing,
            result_sha256=sha(directory/'result.json'),input_sha256=sha(directory/'input.json'),
            sources=[dict(file_id=f['file_id'],sha256=f['file']['sha256']) for f in load(directory/'routes/manifest.json')['files']],
            copied_payload=native['message_payload'])
        summary['cases'][name]=case
    save(args.output/'summary.json',summary)
    headers=['条件','端点 / EP','原始请求','实际消息','有效写入字节','发送字节含重传','缓冲峰值','排空后保留','故障丢包']
    rows=[]
    for label,key in [('人工功能输入','synthetic'),('真实R1专家选择','real_routes')]:
        c=summary['cases'][key];v=c['verification']
        rows.append([label,'128 / '+','.join(map(str,c['ep_sizes'])),c['requests'],v['messages'],v['target_written_bytes'],
                     v['source_packet_payload_bytes'],v['peak_arena_bytes'],v['arena_bytes_at_drain'],c['fault']['dropped_packets']])
    table='<table><thead><tr>'+''.join('<th>'+x+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join(
        '<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table>'
    lifetimes=summary['cases']['real_routes']['example_buffer_lifetimes'];details=[]
    for key,label in [('dispatch_message','原件输入'),('input_message','备用输入'),('result_message','备用结果'),('combine_message','原件结果')]:
        m=lifetimes[key]
        details.append('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in [label,f"{m['src']} → {m['dst']}",
            m['source_address'],m['target_address'],m['post_ns']/1000,m['receive_ready_ns']/1000,m['ack_ns']/1000,
            m['target_released_ns']/1000])+'</tr>')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>128端点Clos：消息缓冲隔离验收</title><style>body{font:16px/1.7 system-ui,sans-serif;max-width:1250px;margin:36px auto;padding:0 22px;color:#172b42;background:#f6f8fb}section{background:white;padding:22px;margin:20px 0;border-radius:12px}h1{font-size:28px}h2{font-size:21px}table{border-collapse:collapse;display:block;overflow:auto;font-size:14px}td,th{padding:10px;border:1px solid #dae1e9;text-align:left;white-space:nowrap}th{background:#edf3f8}a{color:#1260a2}strong{color:#16574c}</style>
<h1>128端点Clos：原任务与备用任务的消息缓冲</h1>
<p>本轮交付的是主服务中的字节与生命周期实现。网络实际收包后写入目标缓冲，原任务和备用任务各占独立范围；源等ACK，目标等接收、ACK与所属任务范围退出。</p>
<section><h2>已验证的结果</h2>'''+table+'''<p>两次独立网络均只运行一次，SR和参考DCQCN开启，各完成一个备用贡献，原图与所有连接照常排空。真实的是专家选择；Packet由声明布局生成，内容为标记字节，计算耗时为声明值。</p></section>
<section><h2>真实路由例：迟到原件与已返回的备用</h2><p>下面全部时间为同一次模拟的微秒。地址是每GPU内的偏移，不同GPU可以出现相同地址。原件尚在等待时，备用输入与备用结果占用另一些范围；备用结束后，原件仍按原协议收包、确认和退出。</p>
<table><thead><tr><th>消息</th><th>源→目的</th><th>源偏移</th><th>目标偏移</th><th>提交µs</th><th>接收就绪µs</th><th>源ACK µs</th><th>目标释放µs</th></tr></thead><tbody>'''+''.join(details)+'''</tbody></table>
<p><strong>备用结果提前到达不等于原推理任务提前完成。</strong>本轮保留原组门限，没有将备用结果接到应用消费链路，不能把表中不同消息的时间差叫作时延收益。</p></section>
<section><h2>如何核查</h2><p>从实际源包十六进制内容和逐包UID追踪到目的端，独立重建乱序接收、重复抑制、跨消息片段，再核对目标写入摘要及所有在途范围。两份运行各拒绝10类篡改，共20项；另有纯C++迟到回调/复用检查和Python乱序、重复、边界用例。</p>
<p>旧默认条件的公开检查通过，旧目录逐项哈希不变；这是旧记录只读兼容核查，没有额外运行默认模式网络。接收描述符本地安装且控制成本为零；不证明RoCE寻址权限、CUDA可见性或数值专家计算。</p></section>
<section><h2>下一步与研究收益</h2><p>下一步需让合法备用贡献被实际应用消费者接受，同时让原任务在后台安全确认和清理，再对比完整早备用基线。必须一起量化请求完成时间、额外字节、计算占用与缓冲峰值。本轮没有新业务收益或论文创新结论。</p>
<p><a href="summary.json">机器可读证据摘要</a> · <a href="../../docs/message_buffers.md">配置与边界</a> · <a href="../../examples/scaleout_message_buffers/README.md">独立复现方法</a></p></section></html>'''
    (args.output/'report.html').write_text(page)
    print(json.dumps(dict(passed=True,cases=2,new_networks=2,corruptions_rejected=20,legacy_unchanged=True)))


if __name__=='__main__':main()
