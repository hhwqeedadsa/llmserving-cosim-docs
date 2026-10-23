"""Independent receive-index enumeration and transcript audit; no producer import."""
import argparse
from collections import Counter, defaultdict
import hashlib
from itertools import permutations
import json
from pathlib import Path


def read(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ('input', 'run', 'analysis', 'repeat', 'output'): p.add_argument('--'+n, type=Path, required=True)
    a = p.parse_args(); assert not a.output.exists()
    obs = a.input/'observed'; truth = a.input/'truth'; tree = read(truth/'tree.json')
    assert tree['sha'] == 'f6aa38fe06cd4a0489e7730365d4e0114970a2f3' and not tree['truncated']
    text = {}
    for row in read(obs/'sources.json'):
        raw = (obs/'sources'/row['path']).read_bytes(); entry = next(e for e in tree['tree'] if e['path']==row['path'])
        assert hashlib.sha256(raw).hexdigest()==row['sha256'] and len(raw)==entry['size']==row['bytes']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==entry['sha']==row['git_blob_sha']
        text[Path(row['path']).name] = raw.decode()
    assert len(text)==6
    # Pinned source obligations, including the copy epilogue that establishes
    # origin-rank-contiguous recv_src_metadata consumed by Combine.
    for file, literal in [('dispatch.cuh', 'atomicAdd(workspace_layout.get_scaleup_atomic_sender_counter() + stored_dst_rank_idx, 1)'),
        ('dispatch_copy_epilogue.cuh', 'get_token_buffer(i - current_rank_start)'),
        ('dispatch_copy_epilogue.cuh', 'current_rank_idx * kNumTopk + master_src_topk_idx'),
        ('combine.cuh', 'math::ceil_div(num_reduced_tokens, kNumSMs * kNumWarps)'),
        ('combine.cuh', 'const int token_start_idx = num_tokens_per_warp * global_warp_idx;'),
        ('combine.cuh', 'const auto global_warp_idx = warp_idx * kNumSMs + sm_idx;'),
        ('barrier.cuh', 'const auto global_channel_idx = sm_idx * kNumChannelsPerSM + channel_in_sm_idx;'),
        ('token.cuh', 'num_topk * (sizeof(int) + sizeof(float))'),
        ('compiled.cuh', 'constexpr int kNumTMAAlignmentBytes = 32;'),
        ('compiled.cuh', 'constexpr int kNumRDMAAlignmentBytes = 64;')]: assert literal in text[file], (file,literal)
    manifest = read(truth/'manifest.json'); real = read(obs/'real.json')
    for rank, item in enumerate(manifest['files']):
        raw = truth/Path(item['file']['path']).name
        assert sha(raw)==item['file']['sha256']
        assert read(raw)[0]['4'][:16]==real['calls'][0]['tokens'][rank]
    assert [len(r) for r in real['calls'][0]['tokens']]==[16,16,10,6]
    checked_events = checked_bindings = 0; summaries = {}; toy_orders = set(); case_rows = []
    for name in ('toy','real'):
        cfg = read(obs/(name+'.json')); plan = read(a.run/(name+'-plan')/'plan.json'); x=cfg['execution']
        call = cfg['calls'][0]; E=cfg['model']['experts']//x['ranks']; counts={}
        expected_slots = {}
        for origin in range(x['ranks']):
            for owner in range(x['ranks']):
                tokens = [t for t,c in enumerate(call['tokens'][origin]) if any(owner*E<=e<(owner+1)*E for e in c)]
                counts[origin,owner] = len(tokens)
                for t in tokens: expected_slots[origin,owner,t] = f'm0.slot.s{origin}.d{owner}.t{t}'
        assert len(plan['slots'])==len(expected_slots)
        assert {(s['src'],s['dst'],s['token']):s['id'] for s in plan['slots']}==expected_slots
        # Directly execute each warp's integer source loop, instead of using
        # producer interval intersections or any platform resolver.
        slots_by_index={}; expected=Counter()
        for owner in range(x['ranks']):
            origins=[s for s in range(x['ranks']) for _ in range(counts[s,owner])]
            per=(len(origins)+x['num_sms']*x['combine_warps']-1)//(x['num_sms']*x['combine_warps'])
            for warp in range(x['combine_warps']):
                for sm in range(x['num_sms']):
                    q=x['call_contexts']
                    if q==1: context=0
                    elif x['num_sms']>q: context=(sm*x['combine_warps']+warp)%q
                    else:
                        assigned=list(range(sm,q,x['num_sms']));context=assigned[warp%len(assigned)]
                    connection=context%x['connections']; local_context=context//x['connections']
                    device=x['local_devices'][owner][connection%len(x['local_devices'][owner])]
                    for index in range(per*(warp*x['num_sms']+sm),min(per*(warp*x['num_sms']+sm+1),len(origins))):
                        origin=origins[index]; key=(0,owner,origin,context,connection,local_context,device)
                        expected[key]+=1;slots_by_index[owner,index]=(key,sm,warp)
        profile=read(a.analysis/(name+'-profile.json'))
        got=Counter({tuple(r[k] for k in ('call','owner','origin','context','connection','local_context','local_device')):r['token_puts'] for r in profile})
        assert got==expected and len(got)==len(profile)
        hidden=2*cfg['model']['hidden_size']; metadata=((8*cfg['model']['top_k']+31)//32)*32
        cbytes=((hidden+metadata+63)//64)*64
        assert cbytes==plan['layout']['combine_put_bytes']
        assert all(r['operand_bytes']==r['token_puts']*cbytes and r['path']==('local' if r['owner']==r['origin'] else 'rdma') for r in profile)
        ops={o['id']:o for o in plan['operations']}; events={e['id']:e for e in plan['events']}
        returns=[o for o in ops.values() if o['phase']=='combine' and o['kind']=='token_data']
        assert {(o['dst'],o['src'],o['token']) for o in returns}==set(expected_slots)
        assert len(returns)==len(expected_slots) and all(o['operand_bytes']==cbytes for o in returns)
        assert all(o['path']==('local' if o['src']==o['dst'] else 'rdma') for o in returns)
        token_slots=defaultdict(set)
        for s in plan['slots']:token_slots[s['src'],s['token']].add(s['id'])
        alloc_signatures=set(); mapping_signatures=[]
        for case in [c for c in read(obs/'cases.json') if c['input']==name]:
            result=read(a.run/(case['name']+'-result.json')); actions=read(a.run/(case['name']+'-actions.json'))['events']
            assert result['all_events_fired'] and len(result['log'])==len(events)==len(actions)
            assert [r['event'] for r in result['log']]==actions
            slots={}; groups=defaultdict(list)
            for row in result['log']:
                if row['kind']=='slot_allocate':
                    s=next(s for s in plan['slots'] if s['id']==row['event']);group=(s['src'],s['dst'])
                    assert row['slot_value']==len(groups[group]);groups[group].append(s['token']);slots[row['event']]=row['slot_value']
            assert slots==result['allocated_slots']
            assert all(len(groups[s,d])==n for (s,d),n in counts.items())
            if name=='toy':toy_orders.add(tuple(groups[0,2]));assert list(groups[0,2])==case['permutation']
            bindings={}; chains=defaultdict(list)
            for op in ops.values():
                if op['phase']!='combine' or op['kind']!='token_data':continue
                owner,origin=op['src'],op['dst'];index=sum(counts[s,owner] for s in range(origin))+slots[op['binding']['slot_variable']]
                key,sm,warp=slots_by_index[owner,index]
                bindings[op['id']]=(key,sm,warp,index);chains[owner,sm,warp].append((index,op))
            extra={}
            for chain in chains.values():
                chain.sort(key=lambda item:item[0])
                for (_,prev),(_,cur) in zip(chain,chain[1:]):
                    reduced,=events[cur['events']['issued']]['deps'];extra[reduced]=prev['events']['issued']
            done=set();observed=Counter(); identity_map={}
            for row in result['log']:
                event=events[row['event']];assert row['event'] not in done and row['kind']==event['kind']
                assert set(event['deps'])<=done
                if row['event'] in extra:assert extra[row['event']] in done
                if row['kind']=='issued':
                    op=ops[row['operation']];assert op['events']['issued']==row['event']
                    if op['phase']=='dispatch' and op['kind']=='token_data':
                        assert token_slots[op['src'],op['token']]<=done
                        assert row['target']['slot_value']==slots[op['slot_variable']]
                        assert row['target']['byte_offset']==op['target']['byte_offset_base']+slots[op['slot_variable']]*op['target']['byte_stride']
                    if op['id'] in bindings:
                        key,sm,warp,index=bindings[op['id']];b=row['binding']
                        assert tuple(b[k] for k in ('context_index','connection','local_context','local_device'))==key[3:]
                        assert (b['sm'],b['rotated_warp'],b['physical_warp'],row['receive_index'])==(sm,warp,(warp-op['src'])%x['combine_warps'],index)
                        observed[key]+=1;checked_bindings+=1
                        if op['path']=='rdma':identity_map[op['id']]=b['connection']
                done.add(row['event']);checked_events+=1
            assert observed==expected and len(done)==len(events)
            alloc_signatures.add(json.dumps(slots,sort_keys=True));mapping_signatures.append(identity_map)
            case_rows.append(dict(name=case['name'],events=len(done),combine_operations=sum(observed.values())))
        changes=sum(len({m[k] for m in mapping_signatures})>1 for k in mapping_signatures[0])
        summary=read(a.analysis/'summary.json')['cases'][name]
        assert summary['distinct_slot_allocations']==len(alloc_signatures) and summary['connection_changing_tokens']==changes
        # Deliberately wrong static model: discard the owner's local-copy
        # tokens before forming the receive-index/channel partition. No new
        # runtime case is executed; this isolates a structural counting error.
        wrong=Counter()
        for owner in range(x['ranks']):
            remote_origins=[s for s in range(x['ranks']) if s!=owner for _ in range(counts[s,owner])]
            per=(len(remote_origins)+x['num_sms']*x['combine_warps']-1)//(x['num_sms']*x['combine_warps'])
            for warp in range(x['combine_warps']):
                for sm in range(x['num_sms']):
                    q=x['call_contexts']
                    if q==1: context=0
                    elif x['num_sms']>q:context=(sm*x['combine_warps']+warp)%q
                    else:
                        assigned=list(range(sm,q,x['num_sms']));context=assigned[warp%len(assigned)]
                    connection=context%x['connections'];local_context=context//x['connections']
                    device=x['local_devices'][owner][connection%len(x['local_devices'][owner])]
                    for i in range(per*(warp*x['num_sms']+sm),min(per*(warp*x['num_sms']+sm+1),len(remote_origins))):
                        wrong[0,owner,remote_origins[i],context,connection,local_context,device]+=1
        remote=Counter({k:v for k,v in expected.items() if k[1]!=k[2]})
        assert sum(wrong.values())==sum(remote.values())
        cells=set(wrong)|set(remote);l1=sum(abs(wrong[k]-remote[k]) for k in cells)
        summaries[name]=dict(allocations=len(alloc_signatures),connection_changing_remote_tokens=changes,
            combine_tokens=sum(expected.values()),nonzero_profile_rows=len(expected),remote_tokens=sum(remote.values()),
            incorrectly_dropping_local_tokens=dict(changed_profile_cells=sum(wrong[k]!=remote[k] for k in cells),
                token_count_l1_difference=l1,minimum_token_mass_reassigned=l1//2,remote_total_unchanged=True))
    assert toy_orders==set(permutations(range(4))) and summaries['toy']['connection_changing_remote_tokens']==4
    repeat_files=sorted(p.name for p in a.analysis.iterdir() if p.is_file())
    assert repeat_files==sorted(p.name for p in a.repeat.iterdir() if p.is_file())
    assert all((a.analysis/n).read_bytes()==(a.repeat/n).read_bytes() for n in repeat_files)
    result=dict(verified=True,sources=6,original_requests=4,original_token_rows=48,toy_permutations=24,
        checked_events=checked_events,checked_combine_bindings=checked_bindings,cases=summaries,
        repeat_analysis_files_byte_identical=repeat_files,declared_untimed_events_only=True,
        network_executed=False,hardware_calibrated=False,packet_or_timing_equivalence_proved=False,
        main_paper_contribution_established=False)
    # Pure scheduling counterexample, in abstract work units, not ns or GPU
    # measurements: equal-size puts, two serial channels of two iterations.
    costs=[9,9,1,1];orders=[[0,1,2,3],[0,2,1,3]]
    lanes=[[sum(costs[t] for t in order[:2]),sum(costs[t] for t in order[2:])] for order in orders]
    assert lanes==[[18,2],[10,10]]
    result['abstract_timing_counterexample']=dict(cost_work_units=costs,slot_orders=orders,channel_work_units=lanes,
        max_channel_work_units=[max(v) for v in lanes],puts_per_channel=[2,2],not_a_timed_or_GPU_experiment=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
