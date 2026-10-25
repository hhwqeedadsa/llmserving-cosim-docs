"""Independent delivery/network comparison; no controller/frontier imports."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from fixtures.native_callback_batches.stage import load,save,sha
from fixtures.session_host_timing.verify import normalized
from fixtures.modern_native.audit import audit


def delivery(root,overrides=None):
    def read(name):return overrides[name] if overrides and name in overrides else load(root/name)
    lines=overrides['control.jsonl'] if overrides and 'control.jsonl' in overrides else [json.loads(line) for line in (root/'control.jsonl').read_text().splitlines()]
    result=read('result.json');modern=read('modern-execution.json')
    assert len(lines)%2==0
    known={};seen=set();callbacks=[];registrations=[];groups=None;logical=0;last=0
    peak_batches=peak_bytes=peak_callbacks=0
    batched='callback_batches' in result['session']
    for index in range(0,len(lines),2):
        incoming,outgoing=lines[index:index+2]
        assert (incoming['direction'],outgoing['direction'])==('native','frontend')
        event,command=incoming['message'],outgoing['message']
        assert event['type']=='DECIDE' and command['type']=='COMMIT'
        assert event['decision_id']==command['decision_id']==index//2 and command['now_ns']==event['now_ns']>=last
        if batched:
            rows=event['batches'];assert 1<=len(rows)<=64
            flat=[];byte_count=count=0
            for j,row in enumerate(rows):
                assert set(row)=={'now_ns','events','drained'} and last<=row['now_ns']<=event['now_ns']
                size=len(json.dumps(row,separators=(',',':')).encode())
                assert size<=65536 and len(row['events'])<=512 and byte_count<65536 and count<512
                byte_count+=size;count+=len(row['events']);assert byte_count<=131072 and count<=1024
                assert row['events']==sorted(row['events'],key=lambda r:r['time_ns'])
                for callback in row['events']:
                    assert callback['operation'] in known and callback['time_ns']<=row['now_ns']
                    key=callback['operation'],callback['event'];assert key not in seen;seen.add(key)
                triggered=groups is None and bool(row['events']) or groups is not None and any(group<=seen for group in groups)
                trigger=(index==0 or row['drained'] or triggered or any('alarm' in r for r in row['events']) or
                         j+1==64 or byte_count>=65536 or count>=512)
                assert trigger==(j==len(rows)-1),'missed or spurious native wake boundary'
                flat.extend(row['events']);last=row['now_ns'];logical+=1
            assert flat==event['events'] and last==event['now_ns'] and rows[-1]['drained']==event['drained']
            peak_batches=max(peak_batches,len(rows));peak_bytes=max(peak_bytes,byte_count);peak_callbacks=max(peak_callbacks,count)
        else:
            for callback in event['events']:
                assert callback['operation'] in known and callback['time_ns']<=event['now_ns']
                key=callback['operation'],callback['event'];assert key not in seen;seen.add(key)
            last=event['now_ns']
        callbacks.extend(event['events'])
        for node in command['submissions']:
            assert node['id'] not in known;known[node['id']]=node
            registrations.append(dict(operation=node['id'],time_ns=event['now_ns'],decision_id=index//2))
        if batched:
            assert 'wake_on' not in command
            groups=[];unique=set();refs=0
            for group in command.get('wake_groups',[]):
                assert group;keys=frozenset((r['operation'],r['event']) for r in group)
                assert len(keys)==len(group) and keys not in unique and not keys&seen
                assert all(i in known for i,_ in keys)
                groups.append(keys);unique.add(keys);refs+=len(keys)
            assert len(groups)<=1280 and refs<=7*1280
    assert callbacks==result['session']['events'] and registrations==result['session']['registrations']
    assert list(known.values())==modern['native_operations']
    assert len(lines)//2==result['session']['decisions']==modern['decisions']<=256
    if batched:
        stats=result['session']['callback_batches'];assert stats==dict(mode='callback_batches/1',logical_steps=logical,
            pending_batches=0,peak_batches=peak_batches,peak_compact_batch_bytes=peak_bytes,peak_callbacks=peak_callbacks,
            physical_decision_limit=256,logical_step_limit=16384)
        assert modern['callback_batches']==dict(mode='callback_batches/1',logical_steps=logical,
            physical_decisions=len(lines)//2,physical_decision_limit=256,logical_step_limit=16384)
    return dict(physical_decisions=len(lines)//2,logical_steps=logical if batched else len(lines)//2,
        callbacks=len(callbacks),native_nodes=len(known),peak_batches=peak_batches,peak_compact_bytes=peak_bytes)


def filtered(result):
    value=deepcopy(result)
    for key in ('callback_wakeups','callback_batches','decisions'):value['session'].pop(key,None)
    for row in value['session']['registrations']:row.pop('decision_id')
    return value


def verify(stage,case,*,captured_root=None):
    root=stage/case;reference='wake-fault' if case=='batch-fault' else 'four-routes' if case=='four-routes' else 'default-healthy'
    old=stage/'original'/reference
    result=load(root/'result.json');modern=load(root/'modern-execution.json')
    assert result['passed'] and result['session']['finished'] and result['drained'] and modern['runtime']['all_events_fired']
    for name in ('initial.json','input.json','protocol.json','topology.json'):assert load(root/name)==load(old/name),name
    before=load(old/'result.json');previous=load(old/'modern-execution.json')
    libraries={Path(path).name:digest for path,digest in load(stage/'identity.json')['libraries'].items()}
    actual=normalized(result,libraries,captured_root);expected=normalized(before,libraries,captured_root)
    if case=='default-healthy':
        assert actual==expected and modern==previous
        assert (root/'control.jsonl').read_bytes()==(old/'control.jsonl').read_bytes()
    elif case!='four-routes':
        assert filtered(actual)==filtered(expected),'native network content/time/order changed'
        m,p=deepcopy(modern),deepcopy(previous)
        m.pop('callback_batches');m.pop('decisions');p.pop('decisions')
        assert m==p,'modern event/content/time/order/bindings/memory changed'
    else:
        for key in ('events','native_operations'):
            assert modern[key][:len(previous[key])]==previous[key],key+' failure prefix changed'
        assert result['session']['events'][:len(before['session']['events'])]==before['session']['events']
        a,b=deepcopy(result['session']['registrations'][:len(before['session']['registrations'])]),deepcopy(before['session']['registrations'])
        for row in a+b:row.pop('decision_id')
        assert a==b
        for key in ('source_packets','source_payload_packets'):
            oldrows=before['transactions']['transport'][key]
            assert result['transactions']['transport'][key][:len(oldrows)]==oldrows
        oldrows=before['packet_capture']['events'];assert result['packet_capture']['events'][:len(oldrows)]==oldrows
    accepted=delivery(root)
    if result['packet_capture']['complete']:application=audit(root)
    else:
        from fixtures.native_callback_batches.application import application_audit
        application=application_audit(root)
    return dict(verified=True,case=case,whole_result_equal=case=='default-healthy',
        network_and_application_equal=case in ('batch-healthy','batch-fault'),
        original_four_rank_prefix_preserved=case=='four-routes',delivery=accepted,application=application,
        capture_complete=result['packet_capture']['complete'],omitted_capture_events=result['packet_capture']['omitted_events'],
        original_decisions=before['session']['decisions'],finish_ns=result['finish_ns'],
        normalization='explicit delivery metadata/counts/physical registration decision ids; verified eight library paths only')


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);p.add_argument('--case',required=True)
    a=p.parse_args();save(a.stage/(a.case+'-verification.json'),verify(a.stage,a.case))


if __name__=='__main__':main()
