"""Publish completed evidence without copying raw source routing or packet captures."""
import argparse
import html
import json
from pathlib import Path
import shutil

from common import ROOT, HERE, load, sha, save, need


def closeout(root, receipt, output):
    output.mkdir(parents=True,exist_ok=False)
    result = load(root/'analysis/analysis.json')
    prepared = load(root/'prepared/preparation.json')
    attempts = {name:load(root/'attempts'/f'{name}.json') for name in ('normal','local')}
    need(result['passed'] and all(a['status']=='verified' for a in attempts.values()), 'unverified research evidence')
    need(attempts['normal']['execution_commit']==attempts['local']['execution_commit'], 'changed execution commit')
    need(load(root/'normal/input.json')==load(root/'local/input.json'), 'changed effective service input')
    normal_protocol,local_protocol = (load(root/name/'rdma.json') for name in ('normal','local'))
    need(normal_protocol=={k:v for k,v in local_protocol.items() if k!='data_loss'}, 'other protocol conditions changed')
    backend = load(receipt)
    for name,digest in {backend['service']:backend['service_sha256'],**backend['libraries'],
                        **{str(ROOT/k):v for k,v in backend['source_hashes'].items()}}.items():
        need(sha(Path(name))==digest,'changed backend/library/source '+name)
    monitors = {str(p.relative_to(root)):load(p) for p in sorted((root/'monitors').glob('*/probe.json'))}
    need(all(p['status']=='completed' and p['child_exit_code']==0 and not p['remaining_live_group_pids']
             for p in monitors.values()), 'failed or live monitored command')
    need(sum(p['wall_seconds'] for p in monitors.values())<900,'time budget exceeded')
    (output/'analysis').mkdir()
    for path in (root/'analysis').iterdir():
        if path.is_file() and path.suffix in ('.json','.csv','.html','.png'):
            shutil.copyfile(path,output/'analysis'/path.name)
    clean,fault = (result['cases'][name] for name in ('normal','local'))
    change = result['contrast']
    relation = '更早' if change['group_extra_ns'][1]<0 else ('更晚' if change['group_extra_ns'][1]>0 else '同一时刻')
    critical = fault['packet']['critical_first_dispatch_messages']
    first_phase = next(p for p in fault['phases'] if p['graph_id']=='g0.w0' and p['layer']==0 and p['phase']=='dispatch')
    normal_phase = next(p for p in clean['phases'] if p['graph_id']=='g0.w0' and p['layer']==0 and p['phase']=='dispatch')
    text = f'''# 去重与卡内合并后的128卡真实路由局部故障

15份不同真实R1-AWQ/MMLU原件、层3/4/Decode入口1/2；128模拟端点、两个EP64组、各8/7请求，每rank每层4专家。16服务器×8卡、8 ToR/8 Agg/4 Core、168链路，各层400G、每链路1us，单rail/plane三层Clos。数据活跃rank共{len(prepared['data_active_ranks'])}个，真实来源请求15个，不是全卡满载或完整671B模型。

normal无人工故障；local只在GPU{prepared['hotspot_rank']}的前100us丢弃目的数据，控制包保留。两者相同任务/连接/网络/SR/DCQCN，按目的rank去重、卡内合并，每份额外贡献声明100ns。各完成{prepared['accounting']['messages']}消息、{prepared['accounting']['layout_bytes']:,}B布局载荷、480专家贡献，归并声明工作累计3400ns；不是硬件测量。

| 条件 | 有限任务 µs | 组0 µs | 组1 µs | 网络排空 µs | 额外源载荷/B |
|---|---:|---:|---:|---:|---:|
| normal | {clean['timing']['application_finished_ns']/1000:.3f} | {clean['groups'][0]['finished_ns']/1000:.3f} | {clean['groups'][1]['finished_ns']/1000:.3f} | {clean['timing']['network_drained_ns']/1000:.3f} | {clean['source_extra_payload_bytes']} |
| local | {fault['timing']['application_finished_ns']/1000:.3f} | {fault['groups'][0]['finished_ns']/1000:.3f} | {fault['groups'][1]['finished_ns']/1000:.3f} | {fault['timing']['network_drained_ns']/1000:.3f} | {fault['source_extra_payload_bytes']} |

## 发现和判定

故障使整段增加{change['application_extra_ns']/1000:.3f}us；健康组{relation}完成，变化{change['group_extra_ns'][1]/1000:+.3f}us。组1没有直接丢包，两个条件分别实际共用{len(clean['packet']['shared_directed_exits'])}/{len(fault['packet']['shared_directed_exits'])}个有向交换机出口。健康组ECN到达{clean['packet']['group_destination_ce_packets'][1]}→{fault['packet']['group_destination_ce_packets'][1]}包，累计逐跳停留{clean['packet']['group_switch_residence_sum_ns'][1]}→{fault['packet']['group_switch_residence_sum_ns'][1]}ns；这些累计值不是业务时延，相关变化也不是单独的因果证明。

组0首Dispatch释放从{normal_phase['released_ns']/1000:.3f}us变为{first_phase['released_ns']/1000:.3f}us，差{change['first_dispatch_gate_extra_ns']/1000:.3f}us；故障条件{first_phase['positive_group_wait_ranks']}个rank有门限等待，最大{first_phase['max_group_wait_ns']/1000:.3f}us，中位{first_phase['median_group_wait_ns']/1000:.3f}us。保留{len(critical)}条并列最后源确认消息，不挑选单条伪装唯一瓶颈。

首Dispatch之后到组0最后完成的时间差变化为{change['fault_group_change_after_first_dispatch_ns']/1000:+.3f}us。该差分拆开了继承等待与后续执行变化；不能把前一阶段、下一轮开始偏移、每rank门限等待再次相加。关键源发包/恢复请求/丢弃/到达/确认时间见JSON，门限由实际前驱与回调验证。

丢包口径：目标窗口{fault['data_loss']['dropped_packets']}/{fault['data_loss']['window_data_packets']}；目标全程{fault['data_loss']['dropped_packets']}/{fault['data_loss']['eligible_data_packets']}；全网端点观察{fault['packet']['destination_data_drops']}/{fault['packet']['destination_data_arrivals']}。局部窗口100%不是全网100%。自然交换机丢弃及PFC另在JSON中列出。

## 收益与后续

本轮没有新算法或可消除收益的证明。目的卡去重/卡内合并是已有通信方式，SR退避也是已有机制。定时器在故障结束后的等待不自动等于可以安全消除的空间；门限等待保留的是当前库必要依赖下界，不能通过删去断言或去掉屏障制造收益。是否拖慢本例的另一个组见上表；单组对照不能证明恢复必然拖慢所有其他任务。

本轮只改进了强基线的数据表示，仍无GIN计数/屏障流量、真实CQ/GPU可见性、hybrid、多context或计算/网络硬件校准。真实的是15份专家选择，部署/到达/计算为声明，remote revision未知。应先根据已观察到的等待与稀疏负载，决定是否需要更有代表性的真实并发、可见故障信号或共享瓶颈场景，再选择网络/系统优化；不继续缩RTO或加故障强度来维持收益。

实际执行固定于`{attempts['normal']['execution_commit']}`。原件保存在`{root}`，没有重跑成功网络、编译、下载、安装、GPU执行或依赖修改。[网页](report.html)、[完整核验与曲线](analysis/report.html)、[阶段差分](analysis/phase-contrast.csv)、[收据](receipt.json)。
'''
    (output/'README.md').write_text(text)
    table_rows = ''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in (name,
        f'{c["timing"]["application_finished_ns"]/1000:.3f}',f'{c["groups"][0]["finished_ns"]/1000:.3f}',
        f'{c["groups"][1]["finished_ns"]/1000:.3f}',f'{c["timing"]["network_drained_ns"]/1000:.3f}'))+'</tr>'
        for name,c in (('normal',clean),('local',fault)))
    critical_rows = ''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in (
        row['message']['id'],f'{row["message"]["src"]}→{row["message"]["dst"]}',
        row['timeout_recovery_requests'],row['first_source_tx_after_fault_end_ns'],
        row['message']['receive_ready_ns'],row['message']['ack_ns']))+'</tr>' for row in critical)
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>128卡真实路由：去重与卡内合并后的故障代价</title><style>body{{max-width:1120px;margin:40px auto;padding:0 20px;font:17px/1.8 sans-serif;color:#18263b}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccd5df;padding:10px}}img{{max-width:100%}}.note{{background:#fff2d8;padding:14px}}a{{color:#2463b7}}</style>
<h1>128卡真实路由：去重与卡内合并后的故障代价</h1><p>两个EP64组共用一次Clos网络。15份不同真实专家选择，各层400G、每链路1us，SR和参考DCQCN固定。
本轮按目的卡去重并卡内合并；每条件{prepared['accounting']['layout_bytes']:,}B布局载荷，保留480专家贡献。</p>
<p>normal无故障；local仅在GPU{prepared['hotspot_rank']}前100us丢弃目的数据，保留控制包。这是局部接收故障，不是物理上行失效。</p>
<table><tr><th>条件</th><th>有限任务/μs</th><th>组0/μs</th><th>组1/μs</th><th>网络排空/μs</th></tr>{table_rows}</table>
<p class="note">整段增加{change['application_extra_ns']/1000:.3f}μs；健康组{relation}，变化{change['group_extra_ns'][1]/1000:+.3f}μs。故障代价不是承诺的优化收益。</p>
<h2>等待是怎样传下去的</h2><p>首Dispatch释放：{normal_phase['released_ns']/1000:.3f}→{first_phase['released_ns']/1000:.3f}μs。
故障条件有{first_phase['positive_group_wait_ranks']}个rank在门限等待，中位{first_phase['median_group_wait_ns']/1000:.3f}μs。
该阶段之后的时间差变化{change['fault_group_change_after_first_dispatch_ns']/1000:+.3f}μs；不能把继承偏移再算一遍。</p>
<table><tr><th>并列最后确认消息</th><th>源→目标</th><th>超时恢复请求</th><th>故障后下一实际TX/ns</th><th>接收完成/ns</th><th>源确认/ns</th></tr>{critical_rows}</table>
<p>表中重试数是恢复请求；实际源数据包另核对。故障结束后的间隔是既有定时器行为，不是已证明可消除的损失。</p>
<h2>累计接受与源确认</h2><img src="analysis/arrival-curves.png" alt="Cumulative accepted and source-confirmed layout payload">
<p>实线接受、虚线源确认；蓝色正常、橙色故障。曲线含布局元数据/填充。健康组未直接丢包；实际共享出口和逐跳停留见核验，但不把累计排队当业务时延。</p>
<h2>如何判断下一步价值</h2><p>当前输入稀疏：128端点中{len(prepared['data_active_ranks'])}个rank有数据，15真实来源请求，两个EP组8/7请求。
结果不足以将恢复对其他组的拖累作为当然前提。应先补代表性并发、故障可观测性或共享瓶颈证据，再选择优化，不能靠缩RTO/加故障制造收益。</p>
<p class="note">真实的是专家选择。没有GPU运行、完整R1、Prefill或硬件校准；direct BF16和零协调成本门限仍不含完整GIN/CQ/GPU可见性与hybrid。</p>
<p><a href="README.md">完整说明</a> · <a href="analysis/report.html">详细曲线与表</a> · <a href="analysis/analysis.json">完整核验</a> · <a href="receipt.json">执行与资源收据</a></p></html>'''
    (output/'report.html').write_text(page+'\n')
    record = dict(passed=True,network_executions=2,execution_commit=attempts['normal']['execution_commit'],
        local_raw_root=str(root),backend=backend,attempts=attempts,completed_monitors_before_closeout=monitors,
        monitored_seconds_before_closeout=sum(p['wall_seconds'] for p in monitors.values()),
        peak_sampled_group_rss_bytes_before_closeout=max(p['peak_sampled_group_rss_bytes'] for p in monitors.values()),
        preparation=prepared,cold_build_validated=False,gpu_executions=0,dependency_modifications=0,
        successful_network_reruns=0,measured_novel_gain=False,
        source_hashes={p.name:sha(p) for p in HERE.iterdir() if p.suffix in ('.py','.md')},
        export_hashes={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()})
    save(output/'receipt.json',record)
    print(json.dumps(dict(passed=True,output=str(output),network_executions=2,contrast=change)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root','backend-receipt','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args = parser.parse_args()
    closeout(args.root.resolve(),args.backend_receipt.resolve(),args.output.resolve())
