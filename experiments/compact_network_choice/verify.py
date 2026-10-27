"""Independent demand enumeration, evidence corruption rejection, and leakage check."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path

from experiments.compact_network_choice.prepare import NAMES, CONDITIONS, load, save, sha
from experiments.compact_network_choice.compact import execute, choose, stage_of


def reject(fn):
    try:fn()
    except (AssertionError,ValueError,KeyError):return
    raise AssertionError('corruption accepted')


def check_source(observed,override=None):
    src=override if override is not None else load(observed/'source.json')
    assert len(src['selected'])==8 and not src['remote_identity_verified']
    for row in src['selected']:assert sha(observed/'routes'/row['name'])==row['sha256']
    assert tuple(row['name'] for row in src['selected'])==tuple(n+'.json' for n in NAMES)


def independent(observed,batch,override=None):
    cfg=load(observed/f'batch{batch}/input.json');plan=load(observed/f'batch{batch}/plan/plan.json')
    summary=override if override is not None else load(observed.parent/'predictions'/f'batch{batch}.json')['summary']
    expected_data={};expected_cells=defaultdict(lambda:Counter(messages=0,operand_bytes=0,framed_bytes=0,reply_bytes=0))
    placements={};remote_count=0
    def add(stage,src,dst,n,reply=0):
        if src==dst:return
        cell=expected_cells[stage,src,dst];cell['messages']+=1;cell['operand_bytes']+=n
        cell['framed_bytes']+=n+32;cell['reply_bytes']+=reply
    for src in range(4):
        for dst in range(4):
            for phase in ('dispatch','combine'):
                for gate in ('entry','exit'):add(phase+'.'+gate,src,dst,8,40)
            add('dispatch.counts',src,dst,8);add('dispatch.counts',src,dst,512)
    for index,name in enumerate(NAMES[:batch]):
        original=load(observed/'routes'/(name+'.json'))[1]['3'][0]
        src,token=index%4,index//4
        assert cfg['modern']['calls'][0]['tokens'][src][token]==original
        owners={e//64 for e in original};placements[src,token]=owners
        for dst in sorted(owners):
            # Fixed BF16 layout: 64B aligned payload with 32B aligned metadata;
            # R1 H7168/Top8 yields 14,464B Dispatch and 14,400B Combine.
            expected_data['dispatch',src,dst,token]=14464
            expected_data['combine',dst,src,token]=14400
            add('dispatch.data',src,dst,14464);add('combine.data',dst,src,14400)
            remote_count+=src!=dst
    got={}
    control=Counter()
    for op in plan['operations']:
        src,dst=op['src'],op['dst']
        if op['kind']=='token_data':
            key=op['phase'],src,dst,op['token'];assert key not in got
            got[key]=op['operand_bytes']
            assert op['path']==('local' if src==dst else 'rdma')
            original_src,owner=(src,dst) if op['phase']=='dispatch' else (dst,src)
            choices=cfg['modern']['calls'][0]['tokens'][original_src][op['token']]
            assert op['topk_positions']==[i for i,e in enumerate(choices) if e//64==owner]
        else:control[op['phase'],op['kind']]+=1
    assert got==expected_data
    assert control==Counter({('dispatch','barrier_signal'):32,('combine','barrier_signal'):32,
                            ('dispatch','rank_count'):16,('dispatch','expert_count_block'):16})
    expected=[dict(stage=s,src=a,dst=b,**v) for (s,a,b),v in sorted(expected_cells.items())]
    assert summary['cells']==expected
    # Check shortest paths independently, excluding intermediate GPUs.
    t=load(observed/'baseline-topology/topology.json');nodes={n['id']:n for n in t['nodes']}
    links=t['links'];deps={r['rank']:r['node'] for r in cfg['deployment']}
    pair_hops={}
    for src in range(4):
        for dst in range(4):
            if src==dst:continue
            start,end=deps[src],deps[dst];dist={start:0};front=[start]
            while front:
                current=front.pop(0)
                for link in links:
                    if current not in (link['src'],link['dst']):continue
                    other=link['dst'] if current==link['src'] else link['src']
                    if other in dist or (nodes[other]['kind']=='gpu' and other!=end):continue
                    dist[other]=dist[current]+1;front.append(other)
            pair_hops[src,dst]=dist[end]
            assert dist[end]==(2 if nodes[start]['pod']==nodes[end]['pod'] else 6)
    return dict(token_owner_pairs=len(expected_data)//2,remote_token_copies_each_phase=remote_count,
        remote_operand_bytes=sum(n for (phase,src,dst,token),n in expected_data.items() if src!=dst),
        independently_checked_cells=len(expected),shortest_rank_pairs=len(pair_hops),operations=len(plan['operations']))


def validate_execution(result,execution,plan):
    assert result['passed'] and result['drained'] and result['session']['finished']
    assert result['packet_capture']['omitted_events']==0
    assert execution['runtime']['all_events_fired']
    seen={};events={e['id']:e for e in plan['events']}
    for row in execution['events']:
        assert row['event'] not in seen
        assert all(p in seen and seen[p]<=row['time_ns'] for p in events[row['event']]['deps'])
        seen[row['event']]=row['time_ns']
    assert set(seen)==set(events)
    transport=result['transactions']['transport'];packets=transport['source_packets']
    payloads=transport['source_payload_packets'];assert len(packets)==len(payloads)
    cursors=Counter()
    for packet,payload in zip(packets,payloads):
        now,stream,src,dst,nic,seq,nbytes,uid=packet
        assert seq==cursors[stream] and now==payload['time_ns'] and stream==payload['stream']
        assert nbytes==len(bytes.fromhex(payload['hex']))<=2000
        cursors[stream]+=nbytes
    assert sum(cursors.values())==sum(s['submitted_bytes'] for s in transport['streams'])
    for s in transport['streams']:
        assert s['snd_nxt']==s['snd_una']==s['published_bytes']==s['submitted_bytes']
        assert cursors[s['id']]==s['submitted_bytes']
    tx=result['transactions']
    assert len({(r['connection'],r['sequence']) for r in tx['target_events']})==len(tx['operations'])==len(tx['target_events'])
    assert not any(r['duplicate'] for r in tx['target_events'])
    assert not tx['active_source_leases'] and not tx['active_local_memory_leases']
    return len(packets),len(seen),sum(cursors.values())


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True)
    a=p.parse_args();s=a.stage.resolve();obs=s/'observed'
    check_source(obs)
    counts={str(b):independent(obs,b) for b in (4,8)}
    assert choose(dict(baseline=3,bandwidth=3,propagation=3))==dict(selected='baseline',tied_best=list(CONDITIONS))
    assert choose(dict(baseline=4,bandwidth=2,propagation=3))['selected']=='bandwidth'
    reject(lambda:stage_of(dict(phase='dispatch',kind='barrier_signal',id='m0.unknown.s0.d0')))
    corrupt=deepcopy(load(s/'predictions/batch4.json')['summary']);corrupt['cells'][0]['stage']='invalid.stage'
    reject(lambda:independent(obs,4,corrupt))
    source=load(obs/'source.json');original=source['selected'][0]['sha256']
    assert sha(obs/'routes'/source['selected'][0]['name'])==original
    corrupt=deepcopy(source);corrupt['selected'][0]['sha256']='0'*64
    reject(lambda:check_source(obs,corrupt))
    packets=events=frame_bytes=0;strict_failures=[]
    for batch in (4,8):
        for name in CONDITIONS:
            root=s/'truth'/f'batch{batch}'/name
            result=load(root/'modern/result.json');ex=load(root/'modern/modern-execution.json');plan=load(root/'modern/modern-plan.json')
            n,e,b=validate_execution(result,ex,plan);packets+=n;events+=e;frame_bytes+=b
            changed=deepcopy(result);changed['packet_capture']['omitted_events']=1
            reject(lambda:validate_execution(changed,ex,plan))
            changed=deepcopy(ex);changed['runtime']['all_events_fired']=False
            reject(lambda:validate_execution(result,changed,plan))
            if not load(root/'comparison.json')['equivalent']:strict_failures.append([batch,name])
    assert strict_failures==[[8,'propagation']] and load(s/'diagnosis.json')['passed']
    # Make truth unavailable and regenerate the previously frozen predictions.
    hidden=s/'truth-hidden-for-leak-check';assert not hidden.exists()
    (s/'truth').rename(hidden)
    try:execute(obs,s/'observed-only-predictions')
    finally:hidden.rename(s/'truth')
    same={p.name:p.read_bytes()==(s/'observed-only-predictions'/p.name).read_bytes() for p in (s/'predictions').iterdir()}
    assert len(same)==2 and all(same.values())
    ledger=load(s/'execution.json');attempts=[r for r in ledger['records'] if r['network']]
    assert len(attempts)==12 and len({r['label'] for r in attempts})==12 and all(r['exit_code']==0 for r in attempts)
    assert ledger['closed'] and ledger['reruns']==0
    save(s/'verification.json',dict(passed=True,independent_demand=counts,independently_checked_source_packets=packets,
        application_events=events,source_MRT1_frame_bytes=frame_bytes,all_networks_completed=12,
        strict_comparison_failures=strict_failures,after_permutation_explained_without_new_network=True,
        future_truth_removed_prediction_byte_identical=same,corruption_classes=['source_hash','stage','capture_omission','false_completion'],
        manual_checks=['seven_phase_classification','stable_tie_rule','unique_best_rule','24_shortest_rank_paths'],new_networks=0))


if __name__=='__main__':main()
