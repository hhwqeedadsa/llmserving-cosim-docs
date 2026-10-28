"""Independent wire replay, complete-workflow arithmetic and paired equality."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN
import hashlib
import json
from pathlib import Path
import statistics


def read(p): return json.loads(p.read_bytes())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v): p.write_text(json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
def fields(body): return body[0],int.from_bytes(body[4:8],'big'),int.from_bytes(body[8:12],'big')&0xffffff


def inspect(topology,work,result):
    assert result['passed'] and result['drained'] and result['native_network_executed']
    if 'packet_capture' in result:
        assert result['packet_capture']['complete'] and not result['packet_capture']['omitted_events']
    rc=result['rc_concurrent']
    # The pinned RC Observe path throws rather than truncates at its 80k cap.
    # Its own complete wire records are independent of optional Fabric capture.
    assert rc['drained'] and 0<len(rc['physical_packets'])<=80000
    qps={q['id']:q for q in work['connections']}; gpus={n['id'] for n in topology['nodes'] if n['kind']=='gpu'}
    assert len({q['dst'] for q in qps.values()})==len(qps)
    edges={}
    for e in topology['links']:
        edges[e['src'],e['src_port']]=(e['dst'],e['dst_port'],e['delay_ns'],e['rate_bps'])
        edges[e['dst'],e['dst_port']]=(e['src'],e['src_port'],e['delay_ns'],e['rate_bps'])
    expected={}; last={}
    for cid,q in qps.items():
        psn=0
        for index,op in enumerate(q['operations']):
            if op['kind']=='write':
                data=bytes.fromhex(op['data_hex']); n=(len(data)+work['mtu']-1)//work['mtu']
                for part in range(n):
                    expected[cid,psn]=dict(opcode=10 if n==1 else 6 if part==0 else 8 if part==n-1 else 7,
                        address=op['address']+part*work['mtu'],start=op['address'],total=len(data),
                        first=part==0,data=data[part*work['mtu']:(part+1)*work['mtu']])
                    psn+=1
            else:
                expected[cid,psn]=dict(opcode=20,address=op['address'],addend=op['addend']); psn+=1
            last[cid,index]=psn-1
    inflight={}; wires={}; counts=Counter()
    for e in rc['physical_packets']:
        packet=bytes.fromhex(e['packet_hex']); body=packet[30:]
        assert packet[:3]==bytes.fromhex('002145') and packet[11]==17
        assert int.from_bytes(packet[4:6],'big')+2==len(packet)
        opcode,qpn,psn=fields(body); feedback=opcode in (17,18)
        cid=(qpn-1)//2 if feedback else (qpn-2)//2; assert (cid,psn) in expected
        q=qps[cid]; src,dst=(q['dst'],q['src']) if feedback else (q['src'],q['dst'])
        uid=e['packet_uid']; counts[e['kind']]+=1
        if e['kind']=='tx':
            peer,port,delay,rate=edges[e['node'],e['port']]
            whole,rem=divmod(len(packet)*8*10**9,rate); assert uid not in inflight
            inflight[uid]=(peer,port,e['time_ns']+delay+whole+(2*rem>=rate))
            if e['node'] in gpus:
                assert e['node']==src and uid not in wires; wires[uid]=body
        else:
            assert inflight.pop(uid)==(e['node'],e['port'],e['time_ns'])
            if e['node'] in gpus:assert e['node']==dst
            if e['kind']=='drop':
                assert (cid,e['node'],opcode,psn)==tuple(work['loss'][k] for k in ('connection','node','opcode','psn'))
        assert wires[uid]==body
    assert not inflight and counts['tx']==counts['rx']+counts['drop']
    assert counts['drop']==int(work['loss'] is not None)
    memories={m['gpu']:bytearray.fromhex(m['data_hex']) for m in work['memories']}
    seen=set(); effects=[]; old_values={}
    for e in rc['endpoint_events']:
        body=bytes.fromhex(e['body_hex']); assert wires[e['packet_uid']]==body
        if e['kind']!='request_received':continue
        opcode,qpn,psn=fields(body); cid=e['connection']; exp=expected[cid,psn]
        assert qpn==2*cid+2 and opcode==exp['opcode']
        address=exp['address']
        if opcode==20:
            assert len(body)==44 and int.from_bytes(body[12:20],'big')==address
            assert int.from_bytes(body[20:24],'big')==1 and int.from_bytes(body[24:32],'big')==exp['addend']
        else:
            offset=28 if exp['first'] else 12
            if exp['first']:
                assert int.from_bytes(body[12:20],'big')==exp['start']
                assert int.from_bytes(body[20:24],'big')==1 and int.from_bytes(body[24:28],'big')==exp['total']
            pad=(body[1]>>4)&3; payload=body[offset:len(body)-4-pad]; assert payload==exp['data']
        if (cid,psn) in seen:continue
        assert psn==sum(c==cid for c,p in seen); seen.add((cid,psn)); memory=memories[qps[cid]['dst']]
        if opcode==20:
            old=int.from_bytes(memory[address:address+8],'little'); old_values[cid,psn]=old
            memory[address:address+8]=((old+exp['addend'])%2**64).to_bytes(8,'little')
            effects.append(dict(kind='fetch_add',connection=cid,psn=psn,old=old))
        else:
            memory[address:address+len(payload)]=payload
            effects.append(dict(kind='write',connection=cid,psn=psn,address=address,bytes=len(payload)))
    assert seen==set(expected) and effects==rc['target_effects']
    assert {m['gpu']:m['data_hex'] for m in rc['memories']}=={g:b.hex() for g,b in memories.items()}
    for state in rc['connections']:
        cid=state['id']; assert len(state['operations'])==len(qps[cid]['operations'])
        assert state['timeouts']==int(work['loss'] is not None and cid==work['loss']['connection'])
        for done in state['operations']:
            index=done['operation']; psn=last[cid,index]; assert done['last_psn']==psn
            atomic=qps[cid]['operations'][index]['kind']=='fetch_add'
            replies=[e for e in rc['endpoint_events'] if e['connection']==cid and e['kind']=='response_received'
                and fields(bytes.fromhex(e['body_hex']))==(18 if atomic else 17,2*cid+1,psn)
                and e['time_ns']==done['completed_ns']]
            assert len(replies)==1
            if atomic:assert done['old_value']==old_values[cid,psn]==int.from_bytes(bytes.fromhex(replies[0]['body_hex'])[16:24],'big')
    assert result['finish_ns']==max(e['time_ns'] for e in rc['physical_packets'])
    return dict(finish_ns=result['finish_ns'],connections=rc['connections'],target_effects=effects,
        physical_tx=counts['tx'],physical_rx=counts['rx'],physical_drop=counts['drop'],
        memory_hashes={str(g):hashlib.sha256(b).hexdigest() for g,b in memories.items()})


def timing(record,index):
    order=('direct','reuse','reuse','direct','direct','reuse')
    assert record['index']==index and record['pair']==index//2 and record['method']==order[index]
    assert record['exit_code']==0 and record['raw_wait_status']==0 and record['includes_waited_descendants']
    assert record['elapsed_wall_ns']==record['end_perf_ns']-record['start_perf_ns']>0
    cpu=(Decimal(str(record['rusage']['ru_utime']))+Decimal(str(record['rusage']['ru_stime'])))*10**9
    # wait4 exposes floating-point seconds; the measurement stores nearest ns.
    # Decimal reconstruction must round too, rather than truncate a value such
    # as 1127567999.99999998, which is a representation artifact, not lost CPU.
    assert record['subtree_CPU_ns']==int(cpu.to_integral_value(rounding=ROUND_HALF_EVEN))>0


def verify_job(stage,index):
    record=read(stage/f'measure-{index}.json'); timing(record,index)
    folder=stage/'workflows'/f'{index}-{record["method"]}'
    execution=read(folder/'execution.json'); assert execution['closed']
    query_rows=read(stage/'inputs/queries.json'); queries={q['id']:q for q in query_rows}
    outputs=read(folder/'summary.json'); assert set(outputs)==set(queries)
    topology=read(stage/'inputs/topology/topology.json')
    identity=read(stage/'inputs/identity.json')
    for run in execution['networks']:
        assert run['state']=='completed'
        actual=read(folder/'native'/run['name']/'result.json')
        assert {Path(n).name for n in actual['loaded_libraries'] if Path(n).name.startswith('libns3')}==set(identity['library_hashes'])
        assert ('packet_capture' in actual)==run['capture']
        summary=inspect(topology,read(stage/run['work']),actual)
        if not run['capture']:assert outputs[run['name']]==summary
    if record['method']=='direct':
        assert len(execution['networks'])==6 and set(execution['decisions'].values())=={'direct'}
    else:
        predictions=read(folder/'predictions.json'); seal=read(folder/'prediction-seal.json')
        assert sha(folder/'predictions.json')==seal['sha256'] and seal['fault_networks_started']==0
        fallbacks=sum(not p['admitted'] for p in predictions.values())
        assert len(execution['networks'])==2+fallbacks
        for query in query_rows:
            p=predictions[query['id']]
            assert execution['decisions'][query['id']]==('admitted' if p['admitted'] else 'fallback')
            if p['admitted']:
                expected={k:p[k] for k in ('finish_ns','connections','target_effects','physical_tx','physical_rx','physical_drop')}
                expected['memory_hashes']={str(m['gpu']):hashlib.sha256(bytes.fromhex(m['data_hex'])).hexdigest() for m in p['memories']}
                assert outputs[query['id']]==expected
            else:
                probe=read(folder/'native'/query['id']/'execution/probe.json')
                assert datetime.fromisoformat(seal['sealed_utc'])<datetime.fromisoformat(probe['started_utc'])
    pair=index//2; first=2*pair; other=first+int(index==first)
    if (stage/f'measure-{other}.json').is_file():
        other_record=read(stage/f'measure-{other}.json')
        other_summary=read(stage/'workflows'/f'{other}-{other_record["method"]}'/'summary.json')
        assert outputs==other_summary,'paired canonical outputs differ'
    return dict(passed=True,index=index,networks=len(execution['networks']),decisions=execution['decisions'])


def analyze(stage,output):
    ledger=read(stage/'execution.json')
    completed=[i for i,j in enumerate(ledger['jobs']) if j['state']=='completed']
    assert completed==list(range(len(completed))) and ledger['closed']
    checked=[verify_job(stage,i) for i in completed]
    rows=[]
    for pair in range(3):
        if not {2*pair,2*pair+1}<=set(completed):continue
        records={read(stage/f'measure-{i}.json')['method']:read(stage/f'measure-{i}.json') for i in (2*pair,2*pair+1)}
        d,r=records['direct'],records['reuse']
        rows.append(dict(pair=pair,direct_cpu_ns=d['subtree_CPU_ns'],reuse_cpu_ns=r['subtree_CPU_ns'],
            direct_wall_ns=d['elapsed_wall_ns'],reuse_wall_ns=r['elapsed_wall_ns'],
            cpu_ratio=d['subtree_CPU_ns']/r['subtree_CPU_ns'],wall_ratio=d['elapsed_wall_ns']/r['elapsed_wall_ns']))
    rejected=[]; original=read(stage/'measure-0.json')
    for kind in ('CPU','wall','order'):
        changed=deepcopy(original)
        if kind=='CPU':changed['subtree_CPU_ns']+=1
        elif kind=='wall':changed['elapsed_wall_ns']+=1
        else:changed['method']='reuse'
        try:timing(changed,0)
        except AssertionError:rejected.append(kind)
        else:raise AssertionError('accepted arithmetic/order tamper')
    original=read(stage/'workflows/0-direct/summary.json'); expected=read(stage/'workflows/1-reuse/summary.json')
    for kind in ('completion','memory'):
        changed=deepcopy(original); first=next(iter(changed))
        if kind=='completion':changed[first]['finish_ns']+=1
        else:changed[first]['memory_hashes']['0']='0'*64
        try:assert changed==expected
        except AssertionError:rejected.append(kind)
        else:raise AssertionError('accepted output tamper')
    total=sum(c['networks'] for c in checked); assert total<=42
    median=statistics.median(p['cpu_ratio'] for p in rows)
    save(output,dict(passed=True,pairs=rows,checks=checked,network_runs=total,
        median_cpu_ratio=median,median_wall_ratio=statistics.median(p['wall_ratio'] for p in rows),
        complete=len(rows)==3,
        engineering_gate=(all(p['cpu_ratio']>1.05 for p in rows) and median>=1.10) if len(rows)==3 else None,
        completed_pairs=len(rows),planned_pairs=3,stop_reason=ledger.get('stop_reason'),
        tamper_classes_rejected=rejected,new_innovation_proven=False,full_CCL=False,
        input_generation_and_existing_build_excluded=True,normal_cache_preparation_included=True))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True); p.add_argument('--job',type=int); args=p.parse_args()
    assert not args.output.exists()
    if args.job is None:analyze(args.stage.resolve(),args.output.resolve())
    else:save(args.output.resolve(),verify_job(args.stage.resolve(),args.job))
