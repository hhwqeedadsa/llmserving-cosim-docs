"""Independent original-route enumeration and scalar/group/source checks."""
import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
from pathlib import Path
import re
from experiments.provider_binding.stage import load,save,sha


def expected(stage):
    cfg=load(stage/'inputs/input.json');x=cfg['execution'];assert x['ranks']==4 and x['call_contexts']==5
    raw=[load(stage/'inputs/raw'/Path(f['path']).name) for f in load(stage/'inputs/provenance.json')['files']]
    result={}
    def add(identity,ci,phase,kind,src,dst,length,context,local):
        path='local' if local else 'gin_self' if src==dst else 'rdma'
        conn=context%2;inner=context//2
        result[identity]=dict(operation=identity,call=ci,phase=phase,kind=kind,src=src,dst=dst,
            path=path,operand_bytes=length,context_index=context,connection=conn,local_context=inner,
            local_device=x['local_devices'][src][conn],sharing=0,
            qp=None if local else ['shared_devcomm',src,conn,inner,dst])
    for ci in range(4):
        entry=1+ci//2;layer=str(3+ci%2)
        choices=[r[entry][layer][0] for r in raw]
        assert choices==[t[0] for t in cfg['calls'][ci]['tokens']]
        destinations=[{e//64 for e in t} for t in choices]
        for s in range(4):
            for d in range(4):
                for phase in ('dispatch','combine'):
                    for boundary in ('entry','exit'):
                        add(f'm{ci}.{phase}.{boundary}.s{s}.d{d}',ci,phase,'barrier_signal',s,d,8,0,False)
                add(f'm{ci}.dispatch.rank_count.s{s}.d{d}',ci,'dispatch','rank_count',s,d,8,0,s==d)
                add(f'm{ci}.dispatch.expert_count.s{s}.d{d}',ci,'dispatch','expert_count_block',s,d,512,0,False)
                if d in destinations[s]:
                    # Token0 occupies first data warp of SM0. Notify reserves0.
                    add(f'm{ci}.dispatch.data.s{s}.t0.d{d}',ci,'dispatch','token_data',s,d,14464,1,s==d)
        # Expanded direct epilogue has source-rank-contiguous receive order.
        # Self/bypass tokens participate in that sequence and its warp ranges.
        for owner in range(4):
            sources=[s for s in range(4) if owner in destinations[s]]
            chunk=(len(sources)+3)//4
            for index,original in enumerate(sources):
                gw=index//chunk;sm=gw%2;warp=gw//2
                context=sm+2*warp
                add(f'm{ci}.combine.data.s{owner}.d{original}.t0',ci,'combine','token_data',owner,original,14400,context,owner==original)
    return result


def check(stage,report,comparison):
    wanted=expected(stage);rows={r['operation']:r for r in report['rows']}
    assert len(rows)==len(report['rows'])==486 and rows==wanted
    plan=load(stage/'plan/plan.json');assert len(plan['operations'])==486
    for o in plan['operations']:
        r=wanted[o['id']]
        for k in ('call','phase','kind','src','dst','path','operand_bytes'):assert o[k]==r[k]
        if o['phase']=='combine' and o['kind']=='token_data':
            b=o['binding'];assert b['source_to_owner_count']==1
            ci=o['call'];owner=o['src'];origin=o['dst']
            tokens=load(stage/'inputs/input.json')['calls'][ci]['tokens']
            starts=[s for s in range(4) if any(e//64==owner for e in tokens[s][0])]
            assert b['source_rank_prefix']==starts.index(origin) and b['owner_received_tokens']==len(starts)
        else:
            for k in ('context_index','connection','local_context','local_device'):assert o['binding'][k]==r[k]
    q=load(stage/'queries.json');actual=(stage/'driver-output.txt').read_text().splitlines();assert len(q)==len(actual)==486
    assert (stage/'queries.txt').read_text()==''.join(' '.join(map(str,r['query']))+'\n' for r in q)
    for index,(query,line) in enumerate(zip(q,actual)):
        r=wanted[query['operation']];values=list(map(int,line.split()))
        assert values==[index,r['context_index'],r['connection'],r['local_context'],10+r['connection'],0]
        if r['kind']!='token_data':expected_query=['d',0,-1,1]
        elif r['phase']=='dispatch':expected_query=['d',0,0,0]
        else:expected_query=['c',r['context_index']%2,r['context_index']//2,0]
        assert query['query']==expected_query
    groups=defaultdict(list)
    for r in wanted.values():
        if r['qp']:groups[tuple(r['qp'])].append(r)
    recorded={tuple(g['qp']):g for g in report['groups']};assert len(recorded)==len(report['groups']) and set(recorded)==set(groups)
    for key,members in groups.items():
        g=recorded[key]
        assert set(g['operations'])=={r['operation'] for r in members} and len(g['operations'])==len(members)
        assert g['roles']==sorted({r['phase']+'.'+r['kind'] for r in members})
        assert g['calls']==sorted({r['call'] for r in members}) and g['phases']==sorted({r['phase'] for r in members})
        assert g['has_data']==any(r['kind']=='token_data' for r in members) and g['has_control']==any(r['kind']!='token_data' for r in members)
    remote={k:v for k,v in groups.items() if k[1]!=k[4]};directions={(k[1],k[4]) for k in remote}
    s=report['summary'];req=load(stage/'inputs/input.json')['execution']['requested_contexts'];allocated=(req+1)//2*2
    assert s==dict(operations=486,provider_operations=sum(bool(r['qp']) for r in wanted.values()),
        local_bypasses=sum(not r['qp'] for r in wanted.values()),active_provider_qps=len(groups),
        active_remote_qps=len(remote),active_self_primary_qps=len(groups)-len(remote),
        data_control_shared_qps=sum(any(r['kind']=='token_data' for r in v) and any(r['kind']!='token_data' for r in v) for v in groups.values()),
        dispatch_combine_shared_qps=sum(len({r['phase'] for r in v})>1 for v in groups.values()),
        multiple_call_shared_qps=sum(len({r['call'] for r in v})>1 for v in groups.values()),
        per_call_active_qps=sum(len({r['call'] for r in v}) for v in groups.values()),
        allocated_contexts=allocated,allocated_main_qps=allocated*16,allocated_self_responder_qps=allocated*4,
        allocated_qp_objects=allocated*20,independent_four_devcomms_allocated_objects=allocated*80,
        legacy_direction_qps=len(directions),directions_with_multiple_modern_qps=sum(sum(k[1]==s and k[4]==d for k in remote)>1 for s,d in directions))
    for role,item in report['roles'].items():
        phase,kind=role.split('.');sub=[r for r in wanted.values() if r['phase']==phase and r['kind']==kind]
        assert item==dict(operations=len(sub),by_path={p:sum(r['path']==p for r in sub) for p in sorted({r['path'] for r in sub})},
            active_qps=len({tuple(r['qp']) for r in sub if r['qp']}),active_contexts=sorted({r['context_index'] for r in sub if r['qp']}))
    # Reconstruct every old SEND mapping independently from graph-node IDs.
    old=load(stage/'legacy/work.json');sendworks={w['id']:w for w in old['work'] if w['kind']=='write'}
    endpoints={s['id']:(s['src']//2,s['dst']//2) for s in old['streams']}
    remap={};mapping=load(stage/'legacy/mapping.json')['nodes'];cfg=load(stage/'inputs/input.json');rawrows=0
    for step in range(2):
        wave=load(stage/'legacy'/f'g0.w{step}.json')
        for route in wave['routes']:
            for token in route['tokens']:
                source=token['source'];src=token['rank']//2;ci=2*step+route['layer']
                assert source['source_entry']==1+step and source['source_layer_id']==3+route['layer']
                assert token['experts']==cfg['calls'][ci]['tokens'][src][0];rawrows+=1
        for node in mapping:
            if node['graph_id']!=wave['graph_id'] or node['kind']!='SEND':continue
            graph=next(n for n in wave['rank_nodes'][str(node['rank'])] if n['id']==node['node_id'])
            ref,=node['completion'];wid=ref['work'];w=sendworks[wid];message=graph['message']
            src,dst=endpoints[w['stream']];assert (message['src'],message['dst'])==(2*src,2*dst)
            assert message['bytes']==w['bytes'];ci=2*step+message['layer'];phase=message['phase']
            identity=(f'm{ci}.dispatch.data.s{src}.t0.d{dst}' if phase=='dispatch' else f'm{ci}.combine.data.s{src}.d{dst}.t0')
            assert identity in wanted;remap[wid]=identity
    assert len(remap)==len(sendworks)==76 and rawrows==16
    pairs=comparison['pairs'];assert len(pairs)==2 and comparison['matched_legacy_writes']==len(remap) and comparison['raw_route_rows_matched']==rawrows
    source=load(stage/'legacy/feedback-summary.json')['cases']['live-instant']['critical_works']
    for pair,(a,b,oa,ob) in zip(pairs,[(90,108,'m2.combine.data.s0.d2.t0','m3.dispatch.data.s0.t0.d2'),
                                      (80,132,'m2.dispatch.data.s2.t0.d0','m3.combine.data.s2.d0.t0')]):
        assert pair['origin']['work']==a and pair['target']['work']==b and remap[a]==oa and remap[b]==ob
        assert [m['work'] for m in source[str(b)]['last_prior_notice']['work_origins']]==[a]
        for side,op in [('origin',oa),('target',ob)]:
            row=pair[side];w=wanted[op];assert row['modern_operation']==op and row['modern_qp']==w['qp'] and row['context_index']==w['context_index']
            assert row['original_ranks']==[w['src'],w['dst']] and row['physical_ranks']==[2*w['src'],2*w['dst']]
            assert row['legacy_stream']==4*w['src']+w['dst']
        assert pair['old_same_qp'] is True and pair['transferred_rates_or_cnp'] is False
        assert pair['modern_same_qp_under_declared_shared_devcomm']==(wanted[oa]['qp']==wanted[ob]['qp'])
    assert report['network_executions']==0


def sources(stage):
    inputs=stage/'inputs'
    for name,h in load(inputs/'manifest.json')['files'].items():assert sha(inputs/name)==h
    for name,h in load(stage/'legacy/identity.json')['files'].items():assert sha(stage/'legacy'/name)==h
    for f in load(inputs/'sources.json'):
        raw=(inputs/'sources'/f['project']/f['path']).read_bytes();tree=load(inputs/(f['project']+'-tree.json'))
        e=next(r for r in tree['tree'] if r['path']==f['path'])
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==e['sha']==f['blob']
    extra=load(stage/'extra/identity.json');raw=(stage/'extra/host.h').read_bytes()
    entry=next(e for e in load(inputs/'nccl-tree.json')['tree'] if e['path']==extra['path'])
    assert len(raw)==extra['bytes']==entry['size'] and sha(stage/'extra/host.h')==extra['sha256']
    assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==entry['sha']==extra['blob']
    assert re.search(r'\n\s*0,\s*/\* ginCounterCount \*/',raw.decode())
    context=(inputs/'sources/deepep/csrc/kernels/comm/context.cpp').read_text()
    assert 'reqs.ginCounterCount' not in context and 'reqs.ginExclusiveContexts = true;' in context
    assert 'const bool needCompanion = (nCounters > 0);' in (inputs/'sources/nccl/src/transport/net_ib/gdaki/gin_host_gdaki.cc').read_text()
    # Check the exact extracted functions by a separate brace-depth scanner.
    originals=[]
    for file,marker in [('sources/deepep/deep_ep/include/deep_ep/comm/barrier.cuh','template <int kNumSMs, int kNumQPs, int kNumChannelsPerSM, bool kWithNotifyWarps = false>'),
                        ('sources/nccl/src/include/nccl_device/impl/gin__funcs.h','template <typename GinType>\nNCCL_DEVICE_INLINE void ncclGinInitCommon(')]:
        text=(inputs/file).read_text();start=text.index(marker);brace=text.index('{',start);depth=0
        for end in range(brace,len(text)):
            depth+=(text[end]=='{')-(text[end]=='}')
            if depth==0:break
        originals.append(text[start:end+1])
    assert (inputs/'original_functions.inc').read_text()=='\n\n'.join(originals)+'\n'
    out=[]
    anchors=[('deepep','csrc/kernels/comm/context.cpp','reqs.ginExclusiveContexts = true;',3),
        ('deepep','csrc/kernels/comm/context.cpp','// SL index',7),
        ('nccl','src/gin/gin_host.cc','reqs->ginTrafficClass != NCCL_CONFIG_UNDEF_INT',1),
        ('nccl','src/transport/net_ib/gdaki/gin_host_gdaki.cc','const int trafficClass = config->trafficClass;',1),
        ('nccl','src/transport/net_ib/gdaki/gin_host_gdaki.cc','const int ib_sl =',4),
        ('nccl','src/transport/net_ib/gdaki/gin_host_gdaki.cc','doca_verbs_ah_attr_set_traffic_class',1),
        ('nccl','src/include/nccl_device/gin/gdaki/gin_gdaki.h','doca_gpu_dev_verbs_qp* qp = loadConst(&gdaki->gdqp) + peer;',1)]
    for project,name,needle,count in anchors:
        lines=(inputs/'sources'/project/name).read_text().splitlines();positions=[i for i,l in enumerate(lines) if needle in l]
        # Per-verb QP lookup repeats: preserve every occurrence, not a chosen one.
        assert positions
        for start in positions:out.append(dict(project=project,path=name,first_line=start+1,last_line=start+count,text='\n'.join(lines[start:start+count])))
    return out


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);p.add_argument('--analysis',type=Path,required=True);p.add_argument('--comparison',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();report=load(a.analysis);comparison=load(a.comparison);basis=sources(a.stage);check(a.stage,report,comparison)
    rejected=[]
    mutations=[('context',lambda r,c:r['rows'][0].__setitem__('context_index',17)),
        ('device',lambda r,c:r['rows'][0].__setitem__('local_device',99)),
        ('peer',lambda r,c:r['rows'][0].__setitem__('dst',3)),
        ('bypass',lambda r,c:r['rows'][0].__setitem__('path','local')),
        ('phase_scope',lambda r,c:r['groups'][0].__setitem__('phases',[])),
        ('call_scope',lambda r,c:r['groups'][0].__setitem__('calls',[])),
        ('allocation',lambda r,c:r['summary'].__setitem__('allocated_qp_objects',37)),
        ('inheritance',lambda r,c:c['pairs'][1].__setitem__('modern_same_qp_under_declared_shared_devcomm',True)),
        ('rank_projection',lambda r,c:c['pairs'][0]['origin'].__setitem__('physical_ranks',[0,2]))]
    for name,change in mutations:
        r,c=deepcopy(report),deepcopy(comparison);change(r,c)
        try:check(a.stage,r,c)
        except AssertionError:rejected.append(name)
        else:raise AssertionError('accepted mutation '+name)
    save(a.output,dict(passed=True,operation_rows_independently_verified=486,legacy_writes_matched=76,original_route_rows=16,
        scalar_function_queries=486,mutations_rejected=rejected,source_excerpts=basis,new_network_executions=0,
        limitation='conditional identity/initialization semantics, not provider execution or hardware idle-state proof'))
    print('486 operations and two legacy ancestry pairs independently checked; nine mutations rejected')


if __name__=='__main__':main()
