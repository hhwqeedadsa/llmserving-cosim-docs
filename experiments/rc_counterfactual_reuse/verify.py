"""Independent actual-wire evaluation; never imports the prediction algorithm."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct


def read(path):return json.loads(path.read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n')


def inspect(topology,work,result):
    assert result['passed'] and result['drained'] and result['native_network_executed']
    rc=result['rc_concurrent'];links={};gpus={n['id'] for n in topology['nodes'] if n['kind']=='gpu'}
    for edge in topology['links']:
        for a,ap,b,bp in [(edge['src'],edge['src_port'],edge['dst'],edge['dst_port']),
                          (edge['dst'],edge['dst_port'],edge['src'],edge['src_port'])]:
            links[a,ap]=b,bp,edge['delay_ns'],edge['rate_bps']
    inflight={};counts=Counter();sources=Counter();events=[];endpoint_tx={}
    for e in rc['physical_packets']:
        wire=bytes.fromhex(e['packet_hex']);assert wire[:3]==bytes.fromhex('002145') and wire[11]==17
        assert struct.unpack_from('!H',wire,4)[0]+2==len(wire)
        opcode=wire[30];qpn=struct.unpack_from('!I',wire,34)[0];psn=struct.unpack_from('!I',wire,38)[0]&0xffffff
        feedback=opcode in (17,18);cid=(qpn-1)//2 if feedback else (qpn-2)//2
        assert 0<=cid<len(work['connections'])
        q=work['connections'][cid];src=q['dst'] if feedback else q['src'];dst=q['src'] if feedback else q['dst']
        counts[e['kind']]+=1;uid=e['packet_uid']
        if e['kind']=='tx':
            peer,port,delay,rate=links[e['node'],e['port']]
            assert uid not in inflight
            # An independently expressed exact rational nearest-ns duration.
            quotient,remainder=divmod(len(wire)*8*10**9,rate)
            duration=quotient+int(2*remainder>=rate)
            inflight[uid]=(peer,port,e['time_ns']+delay+duration)
            if e['node'] in gpus:
                assert e['node']==src
                sources['response' if feedback else 'request']+=1
                endpoint_tx[uid]=(cid,opcode,psn,e['time_ns'])
        else:
            assert e['kind'] in ('rx','drop')
            assert inflight.pop(uid)==(e['node'],e['port'],e['time_ns'])
            if e['node'] in gpus:assert e['node']==dst
            if e['kind']=='drop':
                assert (cid,e['node'],opcode,psn)==tuple(work['loss'][k] for k in ('connection','node','opcode','psn'))
        events.append(dict(kind=e['kind'],node=e['node'],port=e['port'],time_ns=e['time_ns'],bytes=len(wire),
            packet='cached_atomic_ack' if feedback else 'replayed_atomic_request',opcode=opcode,connection=cid,psn=psn))
    assert not inflight and counts['tx']==counts['rx']+counts['drop']
    assert counts['drop']==int(work['loss'] is not None)
    memories={row['gpu']:bytearray.fromhex(row['data_hex']) for row in work['memories']}
    assert len({q['dst'] for q in work['connections']})==len(work['connections'])
    for q in work['connections']:
        memory=memories[q['dst']]
        for op in q['operations']:
            address=op['address']
            if op['kind']=='write':
                data=bytes.fromhex(op['data_hex']);memory[address:address+len(data)]=data
            else:
                old=int.from_bytes(memory[address:address+8],'little')
                memory[address:address+8]=((old+op['addend'])%2**64).to_bytes(8,'little')
    assert {m['gpu']:m['data_hex'] for m in rc['memories']}=={g:data.hex() for g,data in memories.items()}
    effects=rc['target_effects'];assert sum(e['kind']=='fetch_add' for e in effects)==len(work['connections'])
    write_effects=Counter((e['connection'],e['psn']) for e in effects if e['kind']=='write')
    expected_writes=Counter((q['id'],psn) for q in work['connections'] for psn in range(56))
    assert write_effects==expected_writes
    for effect in effects:
        if effect['kind']=='write':
            assert effect['address']==effect['psn']*256 and effect['bytes']==256
        else:assert effect['psn']==56 and effect['old']==0
    for state in rc['connections']:
        assert len(state['operations'])==2 and state['operations'][-1]['old_value']==0
        expected=0 if work['loss'] is None or state['id']!=work['loss']['connection'] else 1
        assert state['timeouts']==expected
        for done in state['operations']:
            matches=[]
            for event in rc['endpoint_events']:
                body=bytes.fromhex(event['body_hex'])
                if event['connection']==state['id'] and event['kind']=='response_received' and \
                   body[0]==(18 if done['operation']==1 else 17) and \
                   struct.unpack_from('!I',body,8)[0]&0xffffff==done['last_psn'] and \
                   event['time_ns']==done['completed_ns']:
                    matches.append(body)
            assert len(matches)==1
            if done['operation']==1:assert struct.unpack_from('!Q',matches[0],16)[0]==done['old_value']
    return dict(finish_ns=result['finish_ns'],connections=rc['connections'],memories=rc['memories'],
        target_effects=effects,physical_tx=counts['tx'],physical_rx=counts['rx'],physical_drop=counts['drop'],
        source_packets=dict(sources),events=events,endpoint_tx=endpoint_tx)


def compare(prediction,actual):
    assert prediction['admitted'] and prediction['reason']=='terminal_feedback_loss_and_idle_retry'
    for key in ('finish_ns','connections','memories','target_effects','physical_tx','physical_rx','physical_drop','source_packets'):
        assert prediction[key]==actual[key],key
    tail=[]
    for row in actual['events']:
        if row['time_ns']>=prediction['retry_ns']:
            assert row['connection']==prediction['connection'] and row['opcode'] in (20,18) and row['psn']==56
            tail.append({key:row[key] for key in ('kind','node','port','time_ns','bytes','packet')})
    assert prediction['tail_events']==tail


def check_hashes(observed,pred):
    for name,digest in pred['observed_hashes'].items():assert sha(observed/name)==digest,name


def execute(stage,output):
    assert not output.exists();pred=read(stage/'prediction.json')
    assert sha(stage/'prediction.json')==read(stage/'prediction-seal.json')['sha256']
    observed=stage/'observed'
    check_hashes(observed,pred)
    selection=read(observed/'selection.json');raw=read(observed/'source-1472.json')
    assert sha(observed/'source-1472.json')==selection['raw_sha256']
    choices=raw[selection['entry']][str(selection['layer'])][selection['token_row']]
    assert choices==selection['expert_ids'] and sorted({e//64 for e in choices}-{0})==selection['destinations']
    topology=read(observed/'topology/topology.json');checked={}
    identity=read(observed/'identity.json')
    for case in ('healthy','ack_loss','data_loss'):
        work=read(observed/(case+'.json'))
        path=observed/'healthy-run/result.json' if case=='healthy' else stage/'truth'/case/'result.json'
        result=read(path);checked[case]=inspect(topology,work,result)
        loaded={Path(name).name for name in result['loaded_libraries'] if Path(name).name.startswith('libns3')}
        assert loaded==set(identity['library_hashes'])
    for name,digest in identity['library_hashes'].items():
        assert sha(stage/'runtime/runtime/lib'/name)==digest
    compare(pred['cases']['ack_loss'],checked['ack_loss'])
    assert not pred['cases']['data_loss']['admitted'] and pred['cases']['data_loss']['reason']=='not_terminal_atomic_ack_loss'
    rejected=[]
    for kind in ('completion','old_value','drop_count','retry_time','admission_reason','input_identity'):
        changed=deepcopy(pred['cases']['ack_loss'])
        if kind=='completion':changed['finish_ns']+=1
        elif kind=='old_value':changed['connections'][0]['operations'][-1]['old_value']+=1
        elif kind=='drop_count':changed['physical_drop']=0
        elif kind=='retry_time':changed['retry_ns']+=1
        elif kind=='admission_reason':changed['reason']='all_losses_are_equivalent'
        try:
            if kind=='input_identity':
                hashes=deepcopy(pred);hashes['observed_hashes'][next(iter(hashes['observed_hashes']))]='0'*64
                check_hashes(observed,hashes)
            else:compare(changed,checked['ack_loss'])
        except (AssertionError,KeyError):rejected.append(kind)
        else:raise AssertionError('accepted tamper: '+kind)
    summary={case:{k:row[k] for k in ('finish_ns','physical_tx','physical_rx','physical_drop','source_packets')}
             for case,row in checked.items()}
    p=pred['cases']['ack_loss']
    save(output,dict(passed=True,cases=summary,tamper_classes_rejected=rejected,
        exact_prediction_and_tail=True,data_loss_correctly_deferred=True,
        ordinary_idle_path_error_ns=p['ordinary_idle_path_finish_ns']-checked['ack_loss']['finish_ns'],
        blind_old_rtt_error_ns=p['blind_old_rtt_finish_ns']-checked['ack_loss']['finish_ns'],
        retained_tx_records=p['retained_baseline_tx_records'],new_tail_tx_records=p['new_tail_tx_records'],
        observed_only_seal_passed=True,real_tensor_or_full_CCL=False,new_innovation_proven=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();execute(a.stage.resolve(),a.output.resolve())
