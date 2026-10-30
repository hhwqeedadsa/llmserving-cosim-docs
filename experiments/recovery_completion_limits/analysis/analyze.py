"""Audit declared completion constraints from saved runs and fixed source files.

Evaluation only: reads actual exported graphs/callbacks, not a recovery policy.
No network execution, counterfactual latency or hardware calibration.
"""
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path


def load(path,maximum=32*2**20):
    assert path.stat().st_size<=maximum,str(path)
    return json.loads(path.read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2,ensure_ascii=False);stream.write('\n')


def sources(modern,interface):
    selected=[(modern,'deep_ep/include/deep_ep/impls/ep/dispatch.cuh','// Barrier to ensure data arrival',4),
        (modern,'deep_ep/include/deep_ep/impls/ep/combine.cuh','// Final barrier to ensure data arrival',4),
        (modern,'deep_ep/include/deep_ep/comm/barrier.cuh','// Flush all QPs by all SMs',24),
        (interface,'deep_ep/buffers/ep.py','async_with_compute_stream: the current stream',3),
        (interface,'deep_ep/utils/event.py','self.event.current_stream_wait()',10)]
    rows=[]
    for folder,path,marker,count in selected:
        manifests=[load(folder/name,2**20) for name in ('file-sources.json','file-sources-completion.json','sources.json')
                   if (folder/name).is_file()]
        metadata=next(row for manifest in manifests for row in (manifest if isinstance(manifest,list) else manifest['sources']) if row['path']==path)
        file=folder/'sources'/path;raw=file.read_bytes();digest=hashlib.sha256(raw).hexdigest()
        assert metadata['sha256']==digest and metadata['commit']=='f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==metadata['git_blob_sha']
        lines=raw.decode().splitlines();start=next(i for i,line in enumerate(lines) if marker in line)
        excerpt=[dict(line=i+1,text=lines[i]) for i in range(start,min(start+count,len(lines)))]
        rows.append(dict(path=path,commit=metadata['commit'],sha256=digest,git_blob_sha=metadata['git_blob_sha'],
            url=f'https://github.com/deepseek-ai/DeepEP/blob/{metadata["commit"]}/{path}#L{start+1}',excerpt=excerpt))
    return rows


def analyze(run):
    config=load(run/'input.json',2**20);result=load(run/'result.json');frontend=load(run/'frontend.json',4*2**20)
    assert result['passed'] and result['gpu_count']==128 and result['service']['service_finished']
    assert result['service_transport']['native']['closed'] and result['service_transport']['native']['physical_drained']
    assert config['phase_completion']['mode']=='ep_transport_flush/1'
    waves={row['graph_id']:load(run/'graphs'/row['graph_id']/'wave.json') for row in frontend['history']}
    nodes={(g,int(rank),n['id']):n for g,w in waves.items() for rank,rows in w['rank_nodes'].items() for n in rows}
    execution={};gates={};messages={m['id']:m for m in result['service_transport']['messages']}
    for line in (run/'execution_events.jsonl').read_text().splitlines():
        row=json.loads(line)
        if row['kind'].startswith('node_'):
            key=row['graph_id'],row['rank'],row['node'],row['kind'];assert key not in execution
            execution[key]=row['time_ns']
    def done(graph,rank,node):return execution[graph,rank,node,'node_complete']
    for row in result['service']['phase_completion']['gates']:
        graph=row['graph_id'];arrivals=[m['arrived_ns'] for m in row['members']];release=row['released_ns']
        assert release==max(arrivals), 'phase release differs from last actual member'
        limiting=[]
        for member in row['members']:
            rank,node=member['rank'],member['node'];spec=nodes[graph,rank,node]
            assert set(spec)=={'id','kind','name','deps','duration_ns'} and spec['kind']=='GATE'
            assert max(done(graph,rank,dep) for dep in spec['deps'])==member['arrived_ns']
            if member['arrived_ns']!=release:continue
            deps=[]
            for dep in spec['deps']:
                if done(graph,rank,dep)!=release:continue
                predecessor=nodes[graph,rank,dep]
                item=dict(rank=rank,node=dep,name=predecessor['name'],kind=predecessor['kind'],time_ns=release)
                if predecessor['kind'] in ('SEND','RECV'):
                    m=messages[predecessor['message']['id']]
                    item.update(message=m['id'],src=m['src'],dst=m['dst'],bytes=m['bytes'],
                                actual_ack_ns=m['ack_ns'],actual_receive_ns=m['receive_ready_ns'])
                deps.append(item)
            limiting.append(dict(rank=rank,gate_node=node,last_predecessors=deps))
        gates[graph,row['id']]=dict(graph_id=graph,id=row['id'],released_ns=release,last_members=limiting)
    tasks={(t['graph_id'],t['rank'],t['node']):t for t in result['service']['shared_compute']['tasks'] if t['kind']=='original'}
    helper_ids={attempt['candidate'][key] for attempt in result['service']['inflight_helpers']['attempts']
                for key in ('input_message','result_message')}
    helper_graph_nodes=[key for key,node in nodes.items() if node.get('message',{}).get('id') in helper_ids]
    assert not helper_graph_nodes, 'helper message is in the original graph; requires a different completion analysis'
    calls=[];bundle_queues=[]
    for line in (run/'contribution_calls.jsonl').read_text().splitlines():
        call=json.loads(line);binding=call['binding'];state=call['state'];graph=binding['graph_id'];layer=binding['logical_layer']
        gate=gates[graph,f'L{layer}.dispatch'];bundles=[]
        merge=next(n for (g,rank,_),n in nodes.items() if g==graph and rank==binding['source_rank'] and n['name']==f'L{layer}.merge')
        for bundle in binding['bundles']:
            rank=bundle['rank'];node=bundle['expert_node'];task=tasks[graph,rank,node]
            original=state['attempts']['original.'+bundle['id']]
            gate_member=next(member['node'] for phase in result['service']['phase_completion']['gates']
                if phase['graph_id']==graph and phase['id']==gate['id'] for member in phase['members'] if member['rank']==rank)
            assert gate_member in nodes[graph,rank,node]['deps']
            assert task['ready_ns']>=gate['released_ns']>=original['input_ready_ns']
            assert original['compute_begin_ns']==task['ready_ns'] and original['compute_done_ns']==task['done_ns']
            if bundle['combine_message'] is not None:
                recv=next(n for (g,r,_),n in nodes.items() if g==graph and r==binding['source_rank'] and n['kind']=='RECV' and
                          n['message']['id']==bundle['combine_message'])
                assert recv['id'] in merge['deps'], 'original Combine receive omitted from merge dependency'
                required_consumer_node=recv['id']
            else:
                assert node in merge['deps'];required_consumer_node=node
            queue_ns=task['start_ns']-task['ready_ns'];bundle_queues.append(queue_ns)
            bundles.append(dict(id=bundle['id'],rank=rank,experts=bundle['experts'],input_ready_ns=original['input_ready_ns'],
                mandatory_gate_ns=gate['released_ns'],compute_issue_ns=task['ready_ns'],actual_compute_start_ns=task['start_ns'],
                compute_done_ns=task['done_ns'],declared_compute_ns=task['duration_ns'],queue_ns=queue_ns,
                input_to_gate_ns=gate['released_ns']-original['input_ready_ns'],
                original_result_ns=original['result_ready_ns'],winner=state['winners'][bundle['id']],
                required_original_consumer_node=required_consumer_node,merge_node=merge['id'],
                original_result_still_in_graph=True,alternative_result_node_in_graph=False))
        assert state['logical_complete_ns']==max(w['time_ns'] for w in state['winners'].values())
        calls.append(dict(call=call['call'],graph_id=graph,layer=layer,source=binding['source_rank'],
            logical_complete_ns=state['logical_complete_ns'],bundles=bundles,
            original_graph_retired_ns=next(h['finished_ns'] for h in frontend['history'] if h['graph_id']==graph)))
    helper_events=[json.loads(line) for line in (run/'helper_events.jsonl').read_text().splitlines()]
    helpers=[]
    for attempt in result['service']['inflight_helpers']['attempts']:
        row=attempt['candidate'];state={e['kind']:e['time_ns'] for e in helper_events if e['id']==attempt['id']}
        call=next(c for c in calls if c['call']==row['call']);bundle=next(b for b in call['bundles'] if b['id']==row['bundle'])
        helpers.append(dict(id=attempt['id'],call=call['call'],bundle=row['bundle'],result_ns=state['result_ready'],
            original_result_ns=bundle['original_result_ns'],contribution_lead_ns=bundle['original_result_ns']-state['result_ready'],
            call_logical_complete_ns=call['logical_complete_ns'],remaining_bundles=[b['id'] for b in call['bundles'] if b['winner']['time_ns']>state['result_ready']],
            direct_original_graph_substitution=False,changed_communication_completion_contract=False))
    hist=Counter(b['input_to_gate_ns'] for c in calls for b in c['bundles'])
    return dict(gpu_endpoints=128,independent_requests=len(frontend['requests']),groups=[len(g) for g in config['parallel']['groups']],
        calls=calls,gates=list(gates.values()),helpers=helpers,call_count=len(calls),bundle_count=len(bundle_queues),
        original_expert_queue_ns_max=max(bundle_queues),original_expert_queue_nonzero_bundles=sum(q>0 for q in bundle_queues),
        input_to_gate_ns_histogram=[dict(ns=ns,count=count) for ns,count in sorted(hist.items())],
        application_finished_ns=max(h['finished_ns'] for h in frontend['history']),network_drained_ns=result['finish_ns'],
        source_hashes={name:sha(run/name) for name in ('input.json','result.json','frontend.json','execution_events.jsonl','contribution_calls.jsonl')},
        scope='actual callbacks and declared graph dependencies; input-to-gate intervals are not removable business time')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('runs','deep-ep-sources','interface-sources','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    evidence=sources(args.deep_ep_sources.resolve(),args.interface_sources.resolve())
    cases={mode:analyze(args.runs.resolve()/mode) for mode in ('normal','local','helper')}
    before={c['call']:c for c in cases['local']['calls']};after={c['call']:c for c in cases['helper']['calls']}
    assert set(before)==set(after)
    changes=[dict(call=k,before_ns=before[k]['logical_complete_ns'],after_ns=after[k]['logical_complete_ns'])
             for k in before if before[k]['logical_complete_ns']!=after[k]['logical_complete_ns']]
    assert not changes  # This frozen fixture has no token-level gain either.
    save(output/'sources.json',evidence)
    report=dict(passed=True,new_network_executions=0,source_contract='fixed DeepEP direct f6aa38fe; not all libraries or versions',
        cases=cases,changed_calls=changes,application_gain_ns=cases['local']['application_finished_ns']-cases['helper']['application_finished_ns'],
        findings=dict(result_alternative_not_connected_to_original_consumers=True,
            async_return_does_not_establish_tensor_readiness=True,
            deleting_original_flush_gate_is_not_valid_for_this_contract=True,
            original_compute_begin_currently_means_ASTRA_issue=True,
            physical_compute_start_source='service.shared_compute.tasks.start_ns',
            general_optimal_recovery_gain_proven=False))
    save(output/'analysis.json',report)
    title='备用结果为什么不能直接推进原调用'
    source_links=''.join(f'<li><a href="{html.escape(s["url"])}">{html.escape(s["path"])}</a>（固定SHA与短摘录见sources.json）</li>' for s in evidence)
    critical=next(c for c in cases['helper']['calls'] if c['call']=='g0.w0.l0.r0.s0')
    rows=''.join(f'<tr><td>{b["id"]}</td><td>{b["input_ready_ns"]/1000:.3f}</td><td>{b["mandatory_gate_ns"]/1000:.3f}</td><td>{b["actual_compute_start_ns"]/1000:.3f}</td><td>{b["winner"]["time_ns"]/1000:.3f}</td></tr>' for b in critical['bundles'])
    (output/'report.html').write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'''+title+'''</title><style>body{font:17px/1.8 system-ui;background:#f2f5f7;color:#1b3343}main{max-width:980px;margin:auto;padding:30px}section{background:white;padding:24px;margin:20px 0;border-radius:10px}h1{line-height:1.4}h2{font-size:23px}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:10px;border-bottom:1px solid #d4dce2}a{color:#185f88}.note{border-left:5px solid #b66e33;padding:16px;background:#fff2e5}code{overflow-wrap:anywhere}</style><main><h1>'''+title+'''</h1>
    <p class="note">本轮没有新增网络实验。对已执行128端点Clos原件的全部16个调用、32个贡献包重新追踪：备用使一个贡献提前，但16个调用的结果到齐时间均未改变，整项任务收益仍为0%。不能把“数据到了”或“异步返回”当作原通信调用结束。</p>
    <section><h2>先明确通信库允许什么</h2><p>固定DeepEP direct版本在Dispatch结束前调用带flush的gpu_barrier，先flush相关QP，再向各rank发信号并等待各rank信号；Combine也有结束屏障。Python的async_with_compute_stream允许当前流不立刻等待，但需要使用结果时仍要遵守返回事件。EventOverlap中的hook在current_stream_wait之后执行，不能理解为某个token一到就可直接消费。</p><ul>'''+source_links+'''</ul><p>这是已有固定源码的复核，没有在线查新、GPU测量或完整库复现。不推广为所有DeepEP版本、低延迟路径或其他库都不能流水化。仿真EP门限只是必要传输约束的零协调成本投影，不等同真实CUDA事件、GIN/CQ或GPU可见性。</p></section>
    <section><h2>沿实际依赖看等待</h2><table><tr><th>贡献包</th><th>输入到达/us</th><th>组门限/us</th><th>原rank计算开始/us</th><th>首个有效结果/us</th></tr>'''+rows+'''</table><p>Dispatch门限最后到达者是源rank0，其原0→6消息源确认在85.060us完成。rank8输入5.100us已到，也必须等该门限。rank7备用结果25.782us已回源，但原图的RECV和专家/归并节点没有连接到这个替代结果；rank8结果89.808us才补齐该token。原首轮图123.834us退休，完整服务175.778us完成。</p><p>全部输入到门限的间隔是诊断量，存在共享依赖和重叠，不能相加成收益或称作都可删除的等待。</p></section>
    <p class="note">这项结论由原图的必要依赖直接推出，本轮实际记录核对用于排除误解，不作为论文创新，也不说明DeepEP存在设计缺陷。删除必要同步可能导致消费未完成的数据，不能当作有效优化。</p><section><h2>三条修改路线的实际门槛</h2><table><tr><th>路线</th><th>必须实现或证明</th><th>当前判断</th></tr><tr><td>单贡献备用</td><td>同token其余贡献及时完成；结果能替代实际消费依赖；原传输安全后台退出</td><td>当前direct图不具备，不能声称恢复完成</td></tr><tr><td>完整调用在另一副本重执行</td><td>完整输入及布局/计数传到合法副本，整个副本组参与独立调用并按真实事件完成，结果回到原请求；计入全部额外成本</td><td>可研究的成熟重试基线，尚未实现，不是新贡献</td></tr><tr><td>修改通信实现，支持分块/贡献级消费</td><td>真实ready信号、输出稳定性、buffer代际、一次归并、迟到写入隔离及数值正确性；与完整调用重试公平比较</td><td>可能的系统改进方向，需要实际接口/内核依据，不能直接删除GATE</td></tr></table></section>
    <section><h2>系统检查还发现什么</h2><p>已修复备用准入记录未严格绑定同步COMMIT时刻的问题：accepted与input_post必须在声明的同一时刻，错误记录在修改贡献状态前拒绝。85项相关单元检查通过，旧三组原件和迁移检查通过，三种错误时刻被拒绝；没有改变已有网络执行。</p><p>另一个需要后续改进的观测语义：原贡献compute_begin来自ASTRA节点issue，遇到共享GPU排队时它表示计算提交，不一定是物理引擎开始。实际开始应读取shared_compute.tasks.start_ns。本次全部原专家贡献的排队为0，故已有时间结论不受此差异影响；这不能替代未来有竞争条件下的真实开始事件通道。</p></section>
    <section><h2>范围与复核</h2><p>四份人工功能路由，31个数据活跃rank，两个EP64组；不是完整R1或生产收益。代码读取实际图、回调与公开报告，不读取故障真值决定策略，也不模拟“删门限后的性能”。报告用于评价，不能作为在线策略直接读取的未来信息。</p><p><a href="analysis.json">全部调用与门限分析</a> · <a href="sources.json">固定源码依据</a></p></section></main></html>''')
    print(json.dumps(dict(passed=True,calls_per_condition=len(before),all_call_gain_zero=not changes,
        application_gain_ns=report['application_gain_ns'],new_network_executions=0)))


if __name__=='__main__':main()
