"""Offline closure of the stopped comparison; never starts a network."""
import argparse
from collections import Counter
from copy import deepcopy
from html import escape
from pathlib import Path

from experiments.route_alignment.analysis.common import NAMES, NETWORKS, load, save, sha
from experiments.route_alignment.analysis.evaluate import audit


def verify_inputs(stage):
    frozen=load(stage/'frozen.json')
    for name,digest in frozen['files'].items():assert sha(stage/name)==digest,name
    observed=stage/'observed';source=load(observed/'source.json')
    assert [r['name'] for r in source['files']]==[n+'.json' for n in NAMES]
    originals=[]
    for row in source['files']:
        path=observed/'routes'/row['name'];assert sha(path)==row['sha256']
        raw=load(path);originals.append([raw[i]['3'][0] for i in range(1,9)])
    assert load(observed/'selection.json')['routes']==originals
    invariant=None
    for alignment in ('aligned','offset'):
        for phase in range(8):
            root=observed/f'{alignment}-{phase}';work=load(root/'work.json');meta=load(root/'meta.json')
            streams={s['id']:(s['src'],s['dst']) for s in work['streams']}
            by_id={w['id']:w for w in work['work']};totals=Counter()
            for t,step in enumerate(meta['rounds']):
                expected=set()
                for src in range(8):
                    token=originals[src][(t+phase+(src if alignment=='offset' else 0))%8]
                    for e in token:
                        if e//32!=src:expected.add((src,e//32))
                dispatch={streams[by_id[i]['stream']] for i in step['dispatch']}
                combine={streams[by_id[i]['stream']] for i in step['combine']}
                assert dispatch==expected and combine=={(d,s) for s,d in expected}
                assert len(step['dispatch'])==len(dispatch) and len(step['combine'])==len(combine)
            for w in work['work']:
                if w['kind']=='write':assert w['bytes']==14336;totals[streams[w['stream']]]+=w['bytes']
            if invariant is None:invariant=totals
            else:assert totals==invariant
    return dict(passed=True,raw_requests=8,input_variants=16,predictions=32,
                same_connection_bytes=True,source_revision_independently_verified=False)


def execute(stage,output):
    output.mkdir()
    inputs=verify_inputs(stage)
    observed=stage/'observed';name='core100-aligned-0'
    w=load(observed/'aligned-0/work.json');m=load(observed/'aligned-0/meta.json')
    t=load(observed/'core100-topology/topology.json');p=load(observed/'core100-protocol.json')
    r=load(stage/'truth'/name/'result.json');metrics=audit(w,m,t,p,r)
    rejected=[]
    for kind in ('callback','capture_omission','port_bytes','source_sequence','packet_header','raw_source_hash'):
        mutated=deepcopy(r)
        try:
            if kind=='callback':mutated['persistent_transport']['work'][0]['acked_ns']=-1
            elif kind=='capture_omission':mutated['packet_capture']['complete']=False
            elif kind=='port_bytes':mutated['ports'][0]['model_tx_bytes']+=1
            elif kind=='source_sequence':mutated['persistent_transport']['source_packets'][0][5]+=1
            elif kind=='packet_header':mutated['packet_capture']['wire_headers']['headers'][0][1]+=1
            else:
                source=load(observed/'source.json');source['files'][0]['sha256']='0'*64
                assert sha(observed/'routes'/source['files'][0]['name'])==source['files'][0]['sha256']
            if kind!='raw_source_hash':audit(w,m,t,p,mutated)
        except (AssertionError,KeyError):rejected.append(kind)
        else:raise AssertionError('tamper not rejected: '+kind)
    predictions=[]
    for network in NETWORKS:
        values=[]
        for alignment in ('aligned','offset'):
            rows=[load(stage/'predictions'/f'{network}-{alignment}-{phase}.json') for phase in range(8)]
            temporal=[v['temporal_link_ns'] for v in rows];marginal=[v['marginal_link_ns'] for v in rows]
            assert max(temporal)-min(temporal)<1e-6 and max(marginal)-min(marginal)<1e-6
            values.append(dict(network=network,alignment=alignment,marginal_link_ns=marginal[0],
                               temporal_link_ns=temporal[0],network_conditions_run=1 if network=='core100' and alignment=='aligned' else 0))
        delta=(values[1]['temporal_link_ns']-values[0]['temporal_link_ns'])/values[0]['temporal_link_ns']
        for v in values:v['predicted_offset_minus_aligned_fraction']=delta
        predictions.extend(values)
    first=load(stage/'predictions'/(name+'.json'))
    errors={method:(first[method]-metrics['application_ns'])/metrics['application_ns']
            for method in ('marginal_link_ns','temporal_link_ns')}
    summary=dict(status='stopped_after_first_auditor_failure',input_verification=inputs,
        planned_network_conditions=32,network_conditions_run=1,remaining_unexecuted=31,
        native_first_condition_passed=True,offline_first_condition_verified=True,comparison_complete=False,
        original_auditor_failure_preserved=True,original_analysis_commit='7cb45b2',
        packet_id_association='actual IP/ports; message_id may be -1 without recovery recording',
        input_predictions_frozen_unchanged=True,tamper_rejected=rejected,
        first_condition=name,metrics=metrics,first_condition_prediction_error=errors,
        predictions=predictions,new_innovation_established=False,benefit_measured=False,
        source_hashes={name:sha(stage/name) for name in ('frozen.json','failures/failure.json','failures/original-evaluate.py')})
    save(output/'summary.json',summary)
    lines=['# 真实路由时间对齐：本轮结果与边界','',
        '本轮只完成一个网络条件。首个检查器误用可缺省的内部任务编号而失败，后续31条件按事前规则停止；原失败、冻结输入和预测保持。修正检查器后仅离线核验，不重跑网络。没有测得对齐收益，也没有证明新创新。','',
        f'八个用户确认来源的完整请求，固定首八步/layer3，EP8/八台单卡服务器。每个变体合计6,221,824B网络token字节，434次WRITE及136个本地节点，56条持久连接；16个输入的逐连接总字节严格一致。激活BF16、每rank专家1000ns均为声明。','',
        '| 条件 | 平均链路模型/us | 逐步链路模型/us | 包级参考/us |','|---|---:|---:|---:|']
    for row in predictions:
        actual=f"{metrics['application_ns']/1000:.3f}（仅起点0）" if row['network_conditions_run'] else '未执行'
        lines.append(f"| {row['network']}/{row['alignment']} | {row['marginal_link_ns']/1000:.3f} | {row['temporal_link_ns']/1000:.3f} | {actual} |")
    lines += ['',f"首条件完成全部570节点，应用及物理排空均为{metrics['application_ns']/1000:.3f}us；3472个源数据包、{metrics['physical_tx']:,}次含ACK/CNP的物理发送、{metrics['cnp_packets']}个CNP。八轮任务依赖、每流连续序号、数据/ACK包尺寸、端点收发、ECMP投影对应的全部端口字节一致。六类篡改拒绝。队列积分由原生全端口summary保存；端点捕获不能独立重建所有交换机瞬时队列，不冒充逐跳捕获。",'',
        f"该单条件中平均链路模型误差{100*errors['marginal_link_ns']:.2f}%，逐步链路模型误差{100*errors['temporal_link_ns']:.2f}%。这只是一个输入下的预测核对，不能推广成准确率、对齐优化收益或强基线被超越。",'',
        '冻结的普通逐步链路模型预计offset相对aligned变化约−0.37%（100G）、−0.69%（25G），均小于事前10%筛选门槛。但预测不代替未执行的31个包级条件，因此真实对齐效应仍未验证。不能据此否定所有模型、层或请求的时序作用，也不追加层/窗口/seed搜索保住假设。','',
        '复现入口与适用范围见实验README。这里是单层通信字节投影，未运行LLMServingSim、现代DeepEP或完整RC操作；A100标签不提供计算实测。来源目录由用户确认，远端修订未独立核实。两个主创新仍未成立，整体研究继续进行。','']
    markdown='\n'.join(lines);(output/'report.md').write_text(markdown)
    table=''.join('<tr><td>'+escape(v['network']+'/'+v['alignment'])+'</td><td>'+f"{v['marginal_link_ns']/1000:.3f}"+'</td><td>'+f"{v['temporal_link_ns']/1000:.3f}"+'</td><td>'+(f"{metrics['application_ns']/1000:.3f}，仅起点0" if v['network_conditions_run'] else '未执行')+'</td></tr>' for v in predictions)
    paragraphs=''.join('<p>'+escape(line)+'</p>' for line in lines if line and not line.startswith(('|','#')))
    html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>真实路由对齐研究</title><style>body{font:17px/1.8 system-ui;margin:40px auto;padding:0 24px;max-width:1040px;color:#182636;background:#f5f7fa}h1{font-size:30px}table{border-collapse:collapse;width:100%;background:white}th,td{padding:12px;border:1px solid #ccd5df;text-align:left}.scope{padding:18px;background:#fff0cd;border-left:5px solid #ae6c00}p{margin:22px 0}a{color:#1257a5}</style><h1>真实路由对齐：收益筛选与执行边界</h1><p class="scope">1 / 32 个包级条件已执行；原检查失败后停止。没有可报告的对齐收益或新创新。</p><table><tr><th>条件</th><th>平均链路/us</th><th>逐步链路/us</th><th>包级参考/us</th></tr>'+table+'</table>'+paragraphs+'<p><a href="summary.json">数据摘要</a> · <a href="report.md">Markdown报告</a></p></html>\n'
    (output/'report.html').write_text(html)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('stage','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();execute(a.stage.resolve(),a.output.resolve())
