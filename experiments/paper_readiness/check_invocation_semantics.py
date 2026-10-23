"""Independent source identity and SM/warp token-loop enumeration; never execute sources."""
import argparse
import ast
from collections import Counter
from itertools import product
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('input','analysis','collection','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists()
    observed=a.input/'observed';identity=load(observed/'sources.json');summary=load(a.analysis/'summary.json')
    trees={}
    for commit,cache in [('f6aa38fe06cd4a0489e7730365d4e0114970a2f3','deepep_modern_20261022'),
        ('12df1a11afad322be5a204a2db890161cbf8131d','nccl_gin_20261022')]:
        tree=load(ROOT/'data/research'/cache/'tree.json');assert not tree['truncated']
        if cache.startswith('deepep'):assert tree['sha']==commit
        else:
            cr=load(ROOT/'data/research'/cache/'commit.json');assert cr['sha']==commit and cr['tree']['sha']==tree['sha']
        trees[commit]={r['path']:r for r in tree['tree'] if r['type']=='blob'}
    texts={}
    for entry in identity['sources']:
        path=observed/'sources'/entry['path'];raw=path.read_bytes();tree=trees[entry['commit']][entry['path']]
        assert sha(path)==entry['sha256'] and len(raw)==entry['bytes']==tree['size']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==entry['git_blob_sha']==tree['sha']
        assert raw==(ROOT/entry['cache_path']).read_bytes();texts[entry['path']]=raw.decode()
    assert len(texts)==19
    for excerpt in identity['excerpts']:
        lines=texts[excerpt['path']].splitlines();begin=excerpt['first_line']-1
        assert '\n'.join(lines[begin:begin+len(excerpt['text'].split('\n'))])==excerpt['text']
    collection=load(a.collection/'sources.json')
    assert collection['complete'] and collection['requests']==6 and collection['requests_per_url']==1 and not collection['executed_downloaded_code']
    for item in collection['sources']:
        assert item['status']=='verified' and item['exit_code']==0
        assert sha(a.collection/'sources'/item['path'])==item['sha256']
        assert (a.collection/'responses'/(item['path'].replace('/','__')+'.body')).read_bytes()==(observed/'sources'/item['path']).read_bytes()
    cls=next(c for c in ast.parse(texts['deep_ep/buffers/ep.py']).body if isinstance(c,ast.ClassDef) and c.name=='EPBuffer')
    fn={n.name:n for n in cls.body if isinstance(n,ast.FunctionDef)}
    for name,values in identity['api_defaults'].items():
        definition=fn[name];names=[x.arg for x in definition.args.args];offset=len(names)-len(definition.args.defaults)
        for key,value in values.items():assert ast.literal_eval(definition.args.defaults[names.index(key)-offset])==value
    py=texts['deep_ep/buffers/ep.py'];barrier=texts['deep_ep/include/deep_ep/comm/barrier.cuh']
    for literal in ('num_allocated_qps = 65 if check_fast_rdma_atomic_support() else 129',
        'num_allocated_qps = 17','num_qps = min(num_sms, 8 + 1)','num_qps = num_sms * 16 + 1',
        'num_sms = handle.num_sms if num_sms == 0 else align(num_sms, 2)'):
        assert literal in py
    assert 'constexpr int kQPStartIdx = static_cast<int>(kWithNotifyWarps);' in barrier
    assert 'const auto global_channel_idx = sm_idx * kNumChannelsPerSM + channel_in_sm_idx;' in barrier
    assert 'return {kQPStartIdx + (global_channel_idx % kNumAvailableQPs), kSharingGrid};' in barrier
    routes=[]
    for src,item in enumerate(load(a.input/'truth/manifest.json')['files']):
        path=ROOT/item['file']['path'];assert sha(path)==item['file']['sha256']
        routes.append(load(path)[0]['4'][:32])
    assert [len(r) for r in routes]==[32,28,10,6]
    assert load(observed/'routes.json')['routes']==[dict(src=i,choices=r) for i,r in enumerate(routes)]
    settings=load(observed/'conditions.json');expected=list(product((4,8,16),(65536,131072)))
    assert [(c['num_sms'],c['shared_memory_bytes']) for c in settings['cases']]==expected
    assert [(c['num_sms'],c['shared_memory_bytes']) for c in summary['cases']]==expected
    layout_bytes=(448+4)*32  # 448 hidden blocks + four 32-byte metadata blocks, already 64B aligned.
    tile_bytes=layout_bytes+32;notify_bytes=3*128*4
    assert layout_bytes==14464 and tile_bytes==summary['dispatch_shared_token_bytes']==14496
    assert notify_bytes==summary['notify_shared_memory_bytes']==1536
    checked=0;projections=0;case_checks=[]
    for row in summary['cases']:
        sms,mem=row['num_sms'],row['shared_memory_bytes']
        # Enumerate how many complete tiles fit, rather than producer floor division.
        warps=sum(notify_bytes+(i+1)*tile_bytes<=mem for i in range(28))
        assert row['num_dispatch_warps']==warps
        num_qps=min(sms,9);assert row['requested_contexts']==17 and row['call_contexts']==num_qps
        tasks=[]
        for source,choices in enumerate(routes):
            for sm in range(sms):
                for warp in range(warps):
                    # This case always shares data contexts across SMs (SMs > QPs-1).
                    context=1+(sm*warps+warp)%(num_qps-1)
                    for token in range(warp*sms+sm,len(choices),warps*sms):
                        for peer in range(4):
                            if peer!=source and any(64*peer<=expert<64*(peer+1) for expert in choices[token]):
                                tasks.append(dict(src=source,token=token,dst=peer,sm=sm,warp=warp,context_index=context,bytes=layout_bytes))
        canonical=lambda t:(t['src'],t['token'],t['dst'])
        assert sorted(tasks,key=canonical)==sorted(row['source_tasks'],key=canonical)
        assert len(tasks)==row['writes']==180 and sum(t['bytes'] for t in tasks)==row['payload_bytes']==2603520
        counts=Counter((t['src'],t['dst'],t['context_index']) for t in tasks)
        assert row['maximum_writes_per_sender_peer_context']==max(counts.values())
        assert row['active_sender_peer_contexts']==len(counts)
        assert row['active_data_context_indices']==sorted({t['context_index'] for t in tasks})
        for projection in row['connection_projections']:
            n=projection['connections'];assert n in (1,2,4)
            # Locate index in a round-robin table, independent of quotient/remainder implementation.
            table=[(local,connection) for local in range(17) for connection in range(n)]
            distributed=Counter();active=set()
            for t in tasks:
                local,connection=table[t['context_index']];distributed[connection]+=1
                active.add((t['src'],t['dst'],connection,local))
            assert projection['per_connection_writes']=={str(i):distributed[i] for i in range(n)}
            assert projection['active_data_qps']==len(active)
            assert projection['allocated_contexts']==next(v for v in range(17,21) if v%n==0)
            projections+=1
        case_checks.append(dict(num_sms=sms,shared_memory_bytes=mem,dispatch_warps=warps,
            active_sender_peer_contexts=len(counts),active_data_contexts=sorted({t['context_index'] for t in tasks}),
            maximum_puts_per_sender_peer_context=max(counts.values()),writes=180))
        checked+=len(tasks)
    # Source control-flow audit for non-hybrid / no-NVLink traffic. No environment helper execution.
    assert 'if self.num_scaleout_ranks > 1 and (rdma_traffic / rdma_gbs) > (nvlink_traffic / nvlink_gbs):' in py
    assert 'bounded_traffic, bounded_gbs = nvlink_traffic, nvlink_gbs' in py
    assert 'num_sms = num_device_sms  # No traffic, e.g., EP=1' in py
    no_nvl_traffic=(1/4)*(1-1/1);rdma_traffic=(4-1)/4
    assert no_nvl_traffic==0 and rdma_traffic>0
    assert summary['automatic_no_nvlink_direct_sm_branch']['selected_rdma_traffic_used_for_bound'] is False
    assert not summary['network_execution'] and not summary['modern_provider_execution'] and not summary['hardware_calibrated']
    a.output.mkdir();result=dict(verified=True,sources=19,excerpts=len(identity['excerpts']),
        source_task_assignments=checked,connection_projections=projections,cases=case_checks,
        source_code_executed=False,network_executed=False,conditional_shared_memory_only=True,
        actual_hardware_configuration_known=False,independent_load=False,main_paper_contribution_established=False)
    (a.output/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
