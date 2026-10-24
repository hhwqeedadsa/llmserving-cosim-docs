"""Lifetime admission rejection and valid continuation through public session API."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from moe_ub_cosim.transaction_session import NativeTransactionSession


def main():
    p=argparse.ArgumentParser()
    for name in ('topology','protocol','work','backend','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();a.output.mkdir(exist_ok=False)
    work=json.loads(a.work.read_text());initial=dict(work,operations=[])
    api=a.output/'api';api.mkdir();seen=set();submitted=set();rejections=[]
    with NativeTransactionSession(a.topology/'topology.json',a.protocol,initial,a.backend,api) as session:
        while (event:=session.receive()) is not None:
            seen.update((e['operation'],e['event']) for e in event['events'])
            ready=[o for o in work['operations'] if o['id'] not in submitted and
                   all((d['operation'],d['event']) in seen for d in o['after'])]
            if not submitted:
                ready=ready[:40]
            elif not rejections:
                # Exceed only the cumulative remote limit. The same candidate
                # contains individually valid not-yet-submitted nodes first.
                extra=deepcopy(work['operations'][0]);extra['id']=1000;extra['address']=60000
                candidate=[o for o in work['operations'] if o['kind']=='remote' and o['id'] not in submitted]+[extra]
                before=(deepcopy(session.config),deepcopy(session.pending),session.sequence,session.transcript.tell())
                assert session.child.poll() is None
                try:session.commit(candidate)
                except ValueError:pass
                else:raise AssertionError('cumulative remote excess accepted')
                assert before==(session.config,session.pending,session.sequence,session.transcript.tell())
                assert session.child.poll() is None
                rejections.append('remote lifetime; live state and transcript unchanged')
            if len(submitted)==len(work['operations']) and event['drained']:
                extra=dict(id=1001,kind='local',rank=0,duration_ns=0,after=[])
                before=(deepcopy(session.config),deepcopy(session.pending),session.sequence,session.transcript.tell())
                try:session.commit([extra])
                except ValueError:pass
                else:raise AssertionError('cumulative local excess accepted')
                assert before==(session.config,session.pending,session.sequence,session.transcript.tell())
                rejections.append('local lifetime after all completed; valid finish follows')
                session.commit([],finish=True)
            else:
                session.commit(ready);submitted.update(o['id'] for o in ready)
    result=json.loads((api/'result.json').read_text())
    assert result['passed'] and result['completed_operations']==96 and result['completed_local_work']==20
    assert len(rejections)==2

    raw=a.output/'native';raw.mkdir()
    initial=deepcopy(initial);initial['execution_limits']['max_remote_operations']=1
    (raw/'initial.json').write_text(json.dumps(initial)+'\n')
    bad=deepcopy(work['operations'][1]);bad['id']=1002
    message=dict(type='COMMIT',decision_id=0,now_ns=0,submissions=[work['operations'][0],bad],finish=False)
    argv=[str(a.backend),str(a.topology/'topology.json'),str(a.protocol),str(raw/'initial.json'),str(raw/'result.json')]
    done=subprocess.run(argv,input=(json.dumps(message)+'\n').encode(),capture_output=True,timeout=15,cwd=raw)
    (raw/'stdout.log').write_bytes(done.stdout);(raw/'stderr.log').write_bytes(done.stderr)
    (raw/'command.json').write_text(json.dumps(dict(argv=argv,message=message,exit_code=done.returncode),indent=2)+'\n')
    result=json.loads((raw/'result.json').read_text());t=result['transactions']
    assert done.returncode!=0 and not result['passed'] and not t['operations']
    assert 'budget' in result['execution_error'] and not t['transport']['source_packets'] and not t['target_events']
    assert not result['local_work'] and not t['active_source_leases'] and not t['active_local_memory_leases']
    (a.output/'verification.json').write_text(json.dumps(dict(api_rejections=rejections,
        valid_continuation_completed=True,native_whole_candidate_rejected_before_mutation=True,
        native_expected_exit=done.returncode),indent=2)+'\n')


if __name__=='__main__':main()
