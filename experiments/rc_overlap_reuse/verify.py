"""Independent input/wire/target replay and counterfactual evaluation."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import struct


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n')


def fields(body):
    return body[0], int.from_bytes(body[4:8],'big'), int.from_bytes(body[8:12],'big') & 0xffffff


def inspect(topology, work, result):
    assert result['passed'] and result['drained'] and result['native_network_executed']
    assert result['packet_capture']['complete'] and not result['packet_capture']['omitted_events']
    rc = result['rc_concurrent']
    qps = {q['id']:q for q in work['connections']}
    gpus = {n['id'] for n in topology['nodes'] if n['kind']=='gpu'}
    assert len({q['dst'] for q in qps.values()})==len(qps)
    edges = {}
    for e in topology['links']:
        edges[e['src'],e['src_port']] = (e['dst'],e['dst_port'],e['delay_ns'],e['rate_bps'])
        edges[e['dst'],e['dst_port']] = (e['src'],e['src_port'],e['delay_ns'],e['rate_bps'])
    expected = {}
    op_last = {}
    for cid, q in qps.items():
        psn = 0
        for index, op in enumerate(q['operations']):
            if op['kind']=='write':
                data = bytes.fromhex(op['data_hex'])
                n = (len(data)+work['mtu']-1)//work['mtu']
                for part in range(n):
                    chunk = data[part*work['mtu']:(part+1)*work['mtu']]
                    expected[cid,psn] = dict(kind='write', opcode=10 if n==1 else 6 if part==0 else 8 if part==n-1 else 7,
                        address=op['address']+part*work['mtu'], data=chunk,
                        first=part==0, total=len(data), start=op['address'])
                    psn += 1
            else:
                expected[cid,psn] = dict(kind='fetch_add', opcode=20,address=op['address'],addend=op['addend'])
                psn += 1
            op_last[cid,index] = psn-1
    in_flight = {}
    counts = Counter()
    source_packets = Counter()
    source_wire = {}
    observed_packets = []
    for e in rc['physical_packets']:
        wire = bytes.fromhex(e['packet_hex'])
        assert wire[:3]==bytes.fromhex('002145') and wire[11]==17
        assert struct.unpack_from('!H',wire,4)[0]+2==len(wire)
        body = wire[30:]
        opcode,qpn,psn = fields(body)
        feedback = opcode in (17,18)
        cid = (qpn-1)//2 if feedback else (qpn-2)//2
        assert cid in qps and (cid,psn) in expected
        q = qps[cid]
        source,dest = (q['dst'],q['src']) if feedback else (q['src'],q['dst'])
        uid = e['packet_uid']
        counts[e['kind']] += 1
        if e['kind']=='tx':
            assert uid not in in_flight
            peer,port,delay,rate = edges[e['node'],e['port']]
            whole,rem = divmod(len(wire)*8*10**9,rate)
            in_flight[uid] = (peer,port,e['time_ns']+delay+whole+(2*rem>=rate))
            if e['node'] in gpus:
                assert e['node']==source
                source_packets['response' if feedback else 'request'] += 1
                source_wire[uid] = (body,cid,e['time_ns'])
        else:
            assert in_flight.pop(uid)==(e['node'],e['port'],e['time_ns'])
            if e['node'] in gpus:
                assert e['node']==dest
            if e['kind']=='drop':
                assert (cid,e['node'],opcode,psn)==tuple(work['loss'][k] for k in ('connection','node','opcode','psn'))
        assert uid in source_wire and source_wire[uid][0]==body
        observed_packets.append(dict(kind=e['kind'],node=e['node'],port=e['port'],time_ns=e['time_ns'],
                                     bytes=len(wire),uid=uid,connection=cid,opcode=opcode,psn=psn))
    assert not in_flight and counts['tx']==counts['rx']+counts['drop']
    assert counts['drop']==int(work['loss'] is not None)
    memories = {m['gpu']:bytearray.fromhex(m['data_hex']) for m in work['memories']}
    seen = set()
    effects = []
    old_values = {}
    for e in rc['endpoint_events']:
        body = bytes.fromhex(e['body_hex'])
        assert source_wire[e['packet_uid']][0]==body
        cid = e['connection']
        opcode,qpn,psn = fields(body)
        if e['kind']!='request_received':
            continue
        assert qpn==2*cid+2
        exp = expected[cid,psn]
        assert opcode==exp['opcode']
        if (cid,psn) in seen:
            continue
        assert psn==sum(c==cid for c,p in seen)
        seen.add((cid,psn))
        memory = memories[qps[cid]['dst']]
        address = exp['address']
        if opcode==20:
            assert len(body)==44 and int.from_bytes(body[12:20],'big')==address
            assert int.from_bytes(body[20:24],'big')==1 and int.from_bytes(body[24:32],'big')==exp['addend']
            old = int.from_bytes(memory[address:address+8],'little')
            memory[address:address+8] = ((old+exp['addend'])%2**64).to_bytes(8,'little')
            old_values[cid,psn] = old
            effects.append(dict(kind='fetch_add',connection=cid,psn=psn,old=old))
        else:
            offset = 28 if exp['first'] else 12
            if exp['first']:
                assert int.from_bytes(body[12:20],'big')==exp['start']
                assert int.from_bytes(body[20:24],'big')==1 and int.from_bytes(body[24:28],'big')==exp['total']
            pad = (body[1]>>4)&3
            payload = body[offset:len(body)-4-pad]
            assert payload==exp['data']
            memory[address:address+len(payload)] = payload
            effects.append(dict(kind='write',connection=cid,psn=psn,address=address,bytes=len(payload)))
    assert seen==set(expected) and effects==rc['target_effects']
    assert {m['gpu']:m['data_hex'] for m in rc['memories']}=={g:b.hex() for g,b in memories.items()}
    for state in rc['connections']:
        cid = state['id']
        assert len(state['operations'])==len(qps[cid]['operations'])
        assert state['timeouts']==int(work['loss'] is not None and cid==work['loss']['connection'])
        for done in state['operations']:
            index = done['operation']
            last = op_last[cid,index]
            assert done['last_psn']==last
            atomic = qps[cid]['operations'][index]['kind']=='fetch_add'
            replies = [e for e in rc['endpoint_events'] if e['connection']==cid and e['kind']=='response_received'
                and fields(bytes.fromhex(e['body_hex']))==(18 if atomic else 17,2*cid+1,last)
                and e['time_ns']==done['completed_ns']]
            assert len(replies)==1
            if atomic:
                assert done['old_value']==old_values[cid,last]==int.from_bytes(bytes.fromhex(replies[0]['body_hex'])[16:24],'big')
    assert result['finish_ns']==max(p['time_ns'] for p in rc['physical_packets'])
    return dict(passed=True,finish_ns=result['finish_ns'],connections=rc['connections'],memories=rc['memories'],
        target_effects=effects,physical_tx=counts['tx'],physical_rx=counts['rx'],physical_drop=counts['drop'],
        source_packets=dict(source_packets),packets=observed_packets,
        memory_hashes={str(g):hashlib.sha256(b).hexdigest() for g,b in memories.items()})


def compare(pred, actual):
    assert pred['admitted'] and pred['reason']=='terminal_feedback_and_port_time_noninterference'
    for key in ('finish_ns','connections','memories','target_effects','physical_tx','physical_rx','physical_drop'):
        assert pred[key]==actual[key],key
    # Select only the new retry/cached-reply attempt, not baseline concurrent packets.
    retry_uids = {p['uid'] for p in actual['packets'] if p['kind']=='tx' and
        p['node'] in (0,3) and p['connection']==pred['connection'] and p['psn']==pred['psn'] and
        p['time_ns']>=pred['retry_ns']}
    tail = [{k:p[k] for k in ('kind','node','port','time_ns','bytes')} for p in actual['packets'] if p['uid'] in retry_uids]
    expected = [{k:p[k] for k in ('kind','node','port','time_ns','bytes')} for p in pred['tail_events']]
    assert tail==expected


def case_check(stage, case, output):
    mode,condition = case.split('_')
    result = stage/('observed/'+mode+'/run/result.json' if condition=='normal' else 'truth/'+mode+'/result.json')
    actual = inspect(read(stage/'observed/topology/topology.json'),
        read(stage/'observed'/mode/(condition+'.json')),read(result))
    save(output,{k:v for k,v in actual.items() if k not in ('packets','memories','target_effects')})


def queue_witness(topology, work, result, cid, psn):
    """Track the actual predecessor occupying each output; no predictor calls."""
    rc = result['rc_concurrent']
    event = next(e for e in rc['endpoint_events'] if e['kind']=='request_sent' and e['connection']==cid
                 and fields(bytes.fromhex(e['body_hex']))[2]==psn)
    uid = event['packet_uid']
    state = next(s for s in rc['connections'] if s['id']==cid)
    before = next(o for o in state['operations'] if o['last_psn']==psn-1)
    ready = before['completed_ns']
    rates = {(p['node'],p['port']):p['rate_bps'] for p in result['ports']}
    hops = []
    for p in rc['physical_packets']:
        if p['packet_uid']!=uid:
            continue
        if p['kind']=='tx':
            blockers = []
            if p['time_ns']>ready:
                for other in rc['physical_packets']:
                    if other['kind']!='tx' or (other['node'],other['port'])!=(p['node'],p['port']):
                        continue
                    rate = rates[p['node'],p['port']]
                    size = len(bytes.fromhex(other['packet_hex']))
                    whole,remainder = divmod(size*8*10**9,rate)
                    end = other['time_ns']+whole+(2*remainder>=rate)
                    if other['time_ns']<p['time_ns'] and end>ready:
                        opcode,qpn,seq = fields(bytes.fromhex(other['packet_hex'])[30:])
                        owner = (qpn-1)//2 if opcode in (17,18) else (qpn-2)//2
                        blockers.append(dict(connection=owner,opcode=opcode,psn=seq,
                            start_ns=other['time_ns'],end_ns=end))
                assert blockers and all(b['connection']!=cid for b in blockers)
            hops.append(dict(node=p['node'],port=p['port'],ready_ns=ready,start_ns=p['time_ns'],
                             waiting_ns=p['time_ns']-ready,blockers=blockers))
        elif p['kind']=='rx':
            ready = p['time_ns']
    assert len(hops)==6
    return hops


def execute(stage, output):
    pred = read(stage/'prediction.json')
    seal = read(stage/'prediction-seal.json')
    assert sha(stage/'prediction.json')==seal['sha256']
    for name,digest in pred['observed_hashes'].items():
        assert sha(stage/'observed'/name)==digest
    raw = stage/'observed/source-1808.json'
    selection = read(stage/'observed/selection.json')
    assert sha(raw)==selection['raw_sha256'] and read(raw)[0]['3'][0]==selection['expert_ids']
    assert sorted({e//64 for e in selection['expert_ids']}-{0})==selection['destinations']
    identity = read(stage/'observed/identity.json')
    assert sha(stage/'runtime/moe-rdma-rc-concurrent')==identity['backend_sha256']
    for name,digest in identity['library_hashes'].items():
        assert sha(stage/'runtime/runtime/lib'/name)==digest
    checked = {}
    for mode in ('terminal','gated'):
        checked[mode] = {}
        for condition in ('normal','loss'):
            path = stage/('observed/'+mode+'/run' if condition=='normal' else 'truth/'+mode)
            result = read(path/'result.json')
            checked[mode][condition] = inspect(read(stage/'observed/topology/topology.json'),
                read(stage/'observed'/mode/(condition+'.json')), result)
            assert {Path(n).name for n in result['loaded_libraries'] if Path(n).name.startswith('libns3')}==set(identity['library_hashes'])
            if condition=='loss':
                started = read(path/'execution/probe.json')['started_utc']
                assert datetime.fromisoformat(seal['sealed_utc'])<datetime.fromisoformat(started)
    summaries = {}
    for mode, values in checked.items():
        normal,loss = values['normal'],values['loss']
        p = pred['cases'][mode]
        if p['admitted']:
            compare(p,loss)
        changes = []
        estimates = {s['id']:s for s in p['ordinary_rto_shift']['connections']}
        for a,b in zip(normal['connections'],loss['connections']):
            assert a['id']==b['id']
            for first,second in zip(a['operations'],b['operations']):
                guess = estimates[a['id']]['operations'][first['operation']]['completed_ns']
                changes.append(dict(connection=a['id'],operation=first['operation'],
                    delta_ns=second['completed_ns']-first['completed_ns'],diagnostic_error_ns=guess-second['completed_ns']))
        summaries[mode] = dict(normal_finish_ns=normal['finish_ns'],loss_finish_ns=loss['finish_ns'],
            admitted=p['admitted'],reason=p['reason'],global_idle_gate=p.get('global_idle_gate'),
            diagnostic_finish_error_ns=p['ordinary_rto_shift']['finish_ns']-loss['finish_ns'],
            completion_changes=changes,physical={c:{k:v[k] for k in ('physical_tx','physical_rx','physical_drop','source_packets')}
                for c,v in values.items()})
    rejected = []
    admitted = [m for m in ('terminal','gated') if pred['cases'][m]['admitted']]
    if admitted:
        mode = admitted[0]
        for kind in ('completion','old_value','drop_count','tail_time','reason'):
            bad = deepcopy(pred['cases'][mode])
            if kind=='completion': bad['finish_ns']+=1
            elif kind=='old_value': bad['connections'][0]['operations'][-1]['old_value']+=1
            elif kind=='drop_count': bad['physical_drop']=0
            elif kind=='tail_time': bad['tail_events'][0]['time_ns']+=1
            else: bad['reason']='trust_global_idle_only'
            try:
                compare(bad,checked[mode]['loss'])
            except (AssertionError,KeyError):
                rejected.append(kind)
            else:
                raise AssertionError('accepted tamper '+kind)
    topology=read(stage/'observed/topology/topology.json')
    work=read(stage/'observed/gated/normal.json')
    witness={condition:queue_witness(topology,work,read(stage/path),1,28)
        for condition,path in [('normal','observed/gated/run/result.json'),('loss','truth/gated/result.json')]}
    saved=sum(h['waiting_ns'] for h in witness['normal'])-sum(h['waiting_ns'] for h in witness['loss'])
    actual=-next(d['delta_ns'] for d in summaries['gated']['completion_changes'] if d['connection']==1 and d['operation']==1)
    assert saved==actual
    save(output,dict(passed=True,cases=summaries,tamper_classes_rejected=rejected,
        peer_wait_witness=witness,peer_wait_reduction_ns=saved,
        sealed_before_both_faults=True,networks=4,full_CCL=False,hardware_calibrated=False,
        full_host_speedup_measured=False,new_innovation_proven=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case')
    args=parser.parse_args()
    assert not args.output.exists()
    if args.case:
        case_check(args.stage.resolve(),args.case,args.output.resolve())
    else:
        execute(args.stage.resolve(),args.output.resolve())
