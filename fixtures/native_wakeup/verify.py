"""Whole-network equivalence and callback delivery oracle; no controller imports."""
import argparse
from copy import deepcopy
from pathlib import Path
from fixtures.native_wakeup.stage import load, save, sha
from fixtures.modern_native.audit import audit as modern_audit, memory_audit, wire_audit


def normalize(result, libraries, *, filtered, captured_library_root=None):
    result=deepcopy(result)
    for index,path in enumerate(result['loaded_libraries']):
        name=Path(path).name
        if name in libraries:
            source=Path(path) if captured_library_root is None else captured_library_root/name
            assert sha(source)==libraries[name], 'loaded ns-3 library identity changed'
            result['loaded_libraries'][index]='verified-fixed-eight/'+name
    if filtered:
        result['session'].pop('callback_wakeups',None)
        result['session'].pop('decisions')
        for row in result['session']['registrations']:row.pop('decision_id')
    return result


def delivery(directory):
    import json
    rows=[json.loads(line) for line in (directory/'control.jsonl').read_text().splitlines()]
    assert len(rows)%2==0
    result=load(directory/'result.json');known={};seen=set();callbacks=[];next_watch=None;registrations=[]
    for index in range(0,len(rows),2):
        incoming,outgoing=rows[index:index+2]
        assert (incoming['direction'],outgoing['direction'])==('native','frontend')
        event,command=incoming['message'],outgoing['message']
        assert event['decision_id']==command['decision_id']==index//2
        assert event['now_ns']==command['now_ns']
        if index and next_watch is not None:
            triggered=any(('alarm' in row) or (row['operation'],row['event']) in next_watch for row in event['events'])
            assert triggered or event['drained'],'frontend woke without a subscribed callback, alarm or drain'
        for row in event['events']:
            if 'alarm' in row:
                key='alarm',row['alarm'],row['event']
            else:
                key=row['operation'],row['event'];assert row['operation'] in known
            assert key not in seen and row['time_ns']<=event['now_ns']
            seen.add(key);callbacks.append(row)
        for node in command['submissions']:
            assert node['id'] not in known;known[node['id']]=node
            registrations.append(dict(operation=node['id'],time_ns=event['now_ns'],decision_id=index//2))
        next_watch=None
        if 'wake_on' in command:
            next_watch=set()
            for reference in command['wake_on']:
                key=reference['operation'],reference['event']
                assert key not in seen and key not in next_watch and key[0] in known
                next_watch.add(key)
    assert callbacks==result['session']['events'], 'actual callback history not delivered exactly once/in order'
    assert registrations==result['session']['registrations'],'registration time, order or decision identity changed'
    assert len(rows)//2==result['session']['decisions']<=256
    filtered=result['session'].get('callback_wakeups')
    if filtered:
        assert filtered['pending_callbacks']==0 and filtered['retained_callbacks']==len(callbacks)
        assert filtered['filtered_commands']==sum('wake_on' in row['message'] for row in rows[1::2])
    return dict(decisions=len(rows)//2,callbacks=len(callbacks),native_submissions=len(known),
                suppressed_event_batches=filtered['suppressed_event_batches'] if filtered else 0)


def verify(stage,case,*,captured_library_root=None):
    directory=stage/case;result=load(directory/'result.json')
    assert result['passed'] and result['session']['finished']
    initial=load(directory/'initial.json');modern=load(directory/'modern-execution.json')
    rows=delivery(directory)
    sourcecase='synthetic-fault' if case=='wake-fault' else 'four-routes' if case=='four-routes' else 'synthetic-healthy'
    original=stage/'original'/sourcecase
    assert initial==load(original/'initial.json')
    assert load(directory/'input.json')==load(original/'input.json')
    assert load(directory/'protocol.json')==load(original/'protocol.json')
    assert load(directory/'topology.json')==load(original/'topology.json')
    libraries={Path(path).name:value for path,value in load(stage/'build-inputs.json')['libraries'].items()}
    before=load(original/'result.json');oldmodern=load(original/'modern-execution.json')
    if case=='four-routes':
        oldevents=oldmodern['events'];assert modern['events'][:len(oldevents)]==oldevents
        callbacks=before['session']['events'];assert result['session']['events'][:len(callbacks)]==callbacks
        oldnodes=oldmodern['native_operations'];assert modern['native_operations'][:len(oldnodes)]==oldnodes
        oldregs=deepcopy(before['session']['registrations']);newregs=deepcopy(result['session']['registrations'][:len(oldregs)])
        for row in [*oldregs,*newregs]:row.pop('decision_id')
        assert oldregs==newregs
        # Compare actual source packets and all captured physical events in
        # their recorded order, including ties at the former failure time.
        for key in ('source_packets','source_payload_packets'):
            old=before['transactions']['transport'][key]
            assert result['transactions']['transport'][key][:len(old)]==old
        old=before['packet_capture']['events'];assert result['packet_capture']['events'][:len(old)]==old
        comparison='all saved failure-prefix events/submissions/packet order retained; full continuation newly executed'
    else:
        filtered=case!='default-healthy'
        assert normalize(result,libraries,filtered=filtered,captured_library_root=captured_library_root)==normalize(before,libraries,filtered=filtered,captured_library_root=captured_library_root), 'whole native network result changed'
        actual,expected=deepcopy(modern),deepcopy(oldmodern)
        if filtered:
            actual.pop('decisions');expected.pop('decisions')
        assert actual==expected,'modern events, bindings, memory state or native work changed'
        comparison='whole native result and modern snapshot equal within explicitly declared control/library fields'
    accepted=modern_audit(directory)
    return dict(passed=True,case=case,comparison=comparison,delivery=rows,modern_acceptance=accepted,
                decision_limit=256,gpu_visibility_modeled=False,hardware_calibrated=False)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',type=Path,required=True)
    parser.add_argument('--case',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();assert not args.output.exists()
    save(args.output,verify(args.stage,args.case))


if __name__=='__main__':main()
