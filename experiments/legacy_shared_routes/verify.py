"""Independent route, byte-range, queue-pair and timing checks; no new network."""
import argparse
from collections import Counter,defaultdict
import gc
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.route_evidence import iter_sources,validate_manifest

if not __debug__:raise RuntimeError('Do not run verification with -O')
ROOT=Path(__file__).resolve().parents[2]
load=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()


def union_size(ranges):
    total=0;end=0
    for a,b in sorted(ranges):
        assert 0<=a<b
        total+=max(0,b-max(end,a));end=max(end,b)
    return total


def check_case(case,study,analysis,selected):
    root=study.parent;run=root/('run-'+case);work=load(run/'execution.json');result=load(run/'result.json')
    published=load(analysis/(case+'-mechanism.json'));audit=load(root/(case+'-audit.json'))
    assert published['identity']['execution_sha256']==sha(run/'execution.json')
    assert published['identity']['result_sha256']==sha(run/'result.json')
    assert published['identity']['audit_sha256']==sha(root/(case+'-audit.json'))
    assert result['passed'] and result['drained'] and audit['passed']
    assert result['gpu_count']==128 and result['switch_count']==20
    assert result['packet_capture']['complete'] and not result['packet_capture']['omitted_events']
    assert result['finish_ns']==published['physical_finish_ns']==audit['physical_finish_ns']
    net=result['legacy_shared']['transport'];offsets=[0,work['groups'][0]['execution']['limits']['streams']]
    assert offsets==[0,15872]
    for group in (0,1):
        execution=work['groups'][group]['execution'];legacy=result['legacy_shared']['groups'][group]['legacy']
        assert execution['ranks']==32 and execution['experts']==256 and execution['hidden_size']==7168
        actual=Counter((op[4],op[6]) for op in execution['operations'] if op[2]==0 and op[3]=='data')
        assert actual==selected[group]
        assert sum(op[3]!='data' for op in execution['operations'])==16384
        maximum=max(t for t,e in zip(legacy['event_times_ns'],execution['events']) if e[0]=='kernel_complete')
        assert maximum==published['groups'][group]['last_kernel_complete_ns']==audit['groups'][group]['last_kernel_complete_ns']
        chain=published['groups'][group]['selected_latest_predecessor_chain']
        for i,item in enumerate(chain):
            event=execution['events'][item['event']];t=legacy['event_times_ns'][item['event']]
            assert item['time_ns']==t and item['name']==event[0] and item['rank']==event[1]
            maximum=max((legacy['event_times_ns'][dep] for dep in event[2]),default=None)
            expected=[d for d in event[2] if legacy['event_times_ns'][d]==maximum]
            assert item['latest_predecessors']==expected
            if item['operation_id'] is not None:
                assert item['operation']==execution['operations'][item['operation_id']]
                assert item['actual_operation']==legacy['operations'][item['operation_id']]
            if i+1<len(chain):assert chain[i+1]['event']==min(expected)
        ranges=defaultdict(list);total=0
        for packet in net['source_packets']:
            _,stream,_,_,_,seq,size,_=packet
            if offsets[group]<=stream<offsets[group]+15872:ranges[stream].append((seq,seq+size));total+=size
        unique=sum(union_size(r) for r in ranges.values())
        assert total==audit['groups'][group]['transmitted_payload_bytes']
        assert unique==audit['groups'][group]['logical_transport_bytes']
        assert total-unique==audit['groups'][group]['retransmitted_payload_bytes']
    # Independent grouped enqueue/dequeue arrays, rather than the analyzer's
    # streaming deque. This case has no queue-clear omissions, so pair all rows.
    queues=defaultdict(lambda:[[],[]]);physical=Counter();exposure=[Counter(),Counter()]
    for row in result['packet_capture']['events']:
        t,node,port,event,uid,stream,proto,seq,_,_,_,_=row
        assert 0<=stream<31744
        group=int(stream>=15872)
        if (node,port) in ((128,17),(132,1)):
            exposure[group][f'event_{event}_protocol_{proto}']+=1
            if 4000<=t<24000:exposure[group][f'during_outage_event_{event}_protocol_{proto}']+=1
        if node>=128 and event in (3,4):queues[node,port,group,uid,stream,proto,seq][event-3].append(t)
        if node>=128 and event==0:physical[node,port,group]+=1
    assert [dict(c) for c in exposure]==published['observations']['bad_edge_exposure'] and not exposure[1]
    accumulated=defaultdict(lambda:[0,0,0]);pairs=0
    for (node,port,group,*_), (enqueues,dequeues) in queues.items():
        assert len(enqueues)==len(dequeues)
        waits=[b-a for a,b in zip(enqueues,dequeues)];assert min(waits,default=0)>=0
        row=accumulated[node,port,group];row[0]+=len(waits);row[1]+=sum(waits);row[2]=max([row[2],*waits]);pairs+=len(waits)
    assert pairs==published['observations']['matched_queue_pairs']
    for row in published['observations']['captured_ports']:
        key=row['node'],row['port'],row['group']
        assert [row['matched_dequeues'],row['sum_residence_packet_ns'],row['max_residence_ns']]==accumulated[key]
        assert row['tx_packets']==physical[key]
    for row in published['observations']['shared_captured_ports']:
        assert all(physical[row['node'],row['port'],g]>0 for g in (0,1))
    assert len(published['observations']['shared_captured_ports'])==8
    assert len(net['dcqcn_reference']['events'])<=200000
    return dict(case=case,passed=True,queue_pairs=pairs,second_group_bad_edge_events=0,
        source_packets=len(net['source_packets']),result_sha256=sha(run/'result.json'))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True)
    p.add_argument('--analysis',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    assert not a.output.exists()
    frozen=load(a.study/'frozen.json')
    for name,expected in frozen['input_sha256'].items():assert sha(a.study/name)==expected
    for name,expected in {**frozen['compiled_sources'],**frozen['code_sha256']}.items():assert sha(ROOT/name)==expected
    for name,expected in frozen['libraries'].items():assert sha(name)==expected
    assert sha(frozen['backend'])==frozen['backend_sha256']
    assert sha(frozen['source_manifest'])==frozen['source_manifest_sha256']
    manifest=load(frozen['source_manifest']);validate_manifest(manifest)
    selected=[Counter(),Counter()];seen=set();files=0
    for i,(_,source) in enumerate(iter_sources(manifest)):
        assert source['file']['sha256'] not in seen;seen.add(source['file']['sha256']);files+=1
        group=int(i>=8);rank=i if i<8 else i-8
        expected=source['raw'][1]['3'][0]
        config=load(a.study/f'group-{group}/input.json')
        assert config['calls'][0]['tokens'][rank]==[expected]
        selected[group].update((rank,e) for e in expected)
    assert files==15 and [sum(s.values()) for s in selected]==[64,56]
    cases=[]
    for case in ('normal','local','prefix'):
        cases.append(check_case(case,a.study,a.analysis,selected));gc.collect()
    (a.output).write_text(json.dumps(dict(passed=True,source_files=15,real_route_selection_verified=True,
        cases=cases,independent_byte_range_union=True,independent_queue_pairing=True,
        hardware_calibrated=False,new_network_runs=0),indent=2)+'\n')
    print(json.dumps(dict(passed=True,source_files=files,verified_cases=3)))


if __name__=='__main__':main()
