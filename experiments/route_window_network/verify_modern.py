"""Independent symbolic-plan validation with raw choices and32x32 controls."""
import argparse
from collections import Counter,deque,defaultdict
import hashlib
import json
from pathlib import Path
import zlib

if not __debug__:raise RuntimeError('Do not run verification with -O')
load=lambda p:json.loads(Path(p).read_bytes())
sha=lambda b:hashlib.sha256(b).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--modern',type=Path,required=True)
    p.add_argument('--admission',type=Path,required=True);p.add_argument('--diagnosis',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();assert not a.output.exists()
    summary=load(a.modern/'summary.json');sizes=load(a.diagnosis/'size-diagnosis.json');records=[]
    for group in (0,1):
        assert summary['groups'][group]['plan_accepted']
        folder=a.modern/f'group-{group}';config=load(folder/'input.json')
        original=load(a.admission/f'group-{group}'/'input.json')
        assert config['calls']==original['calls']
        meta=load(folder/'plan/manifest.json');stored=(folder/'plan/plan.json.zlib').read_bytes()
        assert meta['encoding']=='zlib-json/1' and meta['plan_file']=='plan.json.zlib'
        decoder=zlib.decompressobj();raw=decoder.decompress(stored,64*2**20+1)
        assert decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail and len(raw)<=64*2**20
        assert len(raw)==meta['plan_bytes']==sizes[group]['plain_bytes'] and sha(raw)==meta['plan_sha256']
        assert len(stored)==meta['stored_plan_bytes'] and sha(stored)==meta['stored_plan_sha256']
        plan=json.loads(raw);operations=plan['operations'];events=plan['events']
        assert len(operations)==meta['operation_count']==sizes[group]['operations']<=16384
        assert len(events)==meta['event_count']==sizes[group]['events']<=65536
        assert plan['scope']['network_executed'] is False and plan['scope']['dynamic_slots_resolved'] is False
        ids={e['id']:e for e in events};assert len(ids)==len(events)
        remaining={e['id']:len(set(e['deps'])) for e in events};successors=defaultdict(list)
        for e in events:
            assert len(e['deps'])==len(set(e['deps']))
            for dep in e['deps']:assert dep in ids;successors[dep].append(e['id'])
        ready=deque(k for k,n in remaining.items() if n==0);seen=set()
        while ready:
            key=ready.popleft();assert key not in seen;seen.add(key)
            for target in successors[key]:
                remaining[target]-=1
                if remaining[target]==0:ready.append(target)
        assert len(seen)==len(events)
        observed=Counter();operation_ids=set();expected_data=Counter();self_control=zero_rank=zero_expert=0
        for call,declared in enumerate(config['calls']):
            for src,tokens in enumerate(declared['tokens']):
                for token,choices in enumerate(tokens):
                    owners=sorted({e//8 for e in choices})
                    for dst in owners:
                        expected_data[call,'dispatch',src,dst,token]+=1
                        expected_data[call,'combine',dst,src,token]+=1
        actual_data=Counter()
        for op in operations:
            assert op['id'] not in operation_ids;operation_ids.add(op['id'])
            call=op['call'];s=op['src'];d=op['dst'];kind=op['kind'];phase=op['phase']
            assert call in (0,1) and 0<=s<32 and 0<=d<32
            for name,event in op['events'].items():assert event in ids
            observed[call,phase,kind,s,d]+=1
            if kind=='barrier_signal':
                assert op['operand_bytes']==8 and op['path']==('gin_self' if s==d else 'rdma')
                self_control+=int(s==d)
            elif kind=='rank_count':
                count=sum(any(e//8==d for e in choices) for choices in config['calls'][call]['tokens'][s])
                assert op['value']==-count-1 and op['operand_bytes']==8
                assert op['path']==('local' if s==d else 'rdma');zero_rank+=int(count==0)
            elif kind=='expert_count_block':
                counts=[sum(expert in choices for choices in config['calls'][call]['tokens'][s]) for expert in range(8*d,8*d+8)]
                assert op['values']==[-n-1 for n in counts] and op['operand_bytes']==64
                assert op['path']==('gin_self' if s==d else 'rdma');self_control+=int(s==d);zero_expert+=sum(n==0 for n in counts)
            elif kind=='token_data':
                actual_data[call,phase,s,d,op['token']]+=1
                assert op['operand_bytes']==(14464 if phase=='dispatch' else 14400)
                assert op['path']==('local' if s==d else 'rdma')
            else:raise AssertionError('Unexpected modern operation kind '+kind)
        assert actual_data==expected_data
        for call in (0,1):
            for s in range(32):
                for d in range(32):
                    assert observed[call,'dispatch','barrier_signal',s,d]==2
                    assert observed[call,'combine','barrier_signal',s,d]==2
                    assert observed[call,'dispatch','rank_count',s,d]==1
                    assert observed[call,'dispatch','expert_count_block',s,d]==1
        assert self_control==2*5*32
        assert meta['simulated_packets'] is False
        records.append(dict(group=group,passed=True,operations=len(operations),events=len(events),
            plain_bytes=len(raw),stored_bytes=len(stored),stored_reduction_fraction=1-len(stored)/len(raw),
            canonical_sha256=sha(raw),stored_sha256=sha(stored),
            preserved_self_peer_control_operations=self_control,zero_rank_count_operations=zero_rank,
            zero_expert_count_elements=zero_expert,all_events_in_independent_topological_closure=True))
    a.output.write_text(json.dumps(dict(passed=True,groups=records,new_network_runs=0,
        native_runtime_acceptance_proven=False,shared_fabric_execution_proven=False),indent=2)+'\n')
    print(json.dumps(records))


if __name__=='__main__':main()
