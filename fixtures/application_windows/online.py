"""Live candidate atomicity through the public session API and raw native protocol.

Two bounded functional sessions: API rejection followed by healthy completion,
and direct native rejection whose saved prefix must exclude the entire bad batch.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from moe_ub_cosim.transaction_session import NativeTransactionSession


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('topology','protocol','work','backend','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();a.output.mkdir(exist_ok=False)
    work=json.loads(a.work.read_text());initial=dict(work,operations=[])
    bad=dict(id=9,kind='local',rank=4,duration_ns=1,application_uses=[10],after=[])
    healthy=a.output/'api';healthy.mkdir();seen=set();submitted=set();rejected=False
    with NativeTransactionSession(a.topology/'topology.json',a.protocol,initial,a.backend,healthy) as session:
        while (event:=session.receive()) is not None:
            seen.update((e['operation'],e['event']) for e in event['events'])
            ready=[o for o in work['operations'] if o['id'] not in submitted and
                   all((d['operation'],d['event']) in seen for d in o['after'])]
            if (0,'completed') in seen and not rejected:
                before=(deepcopy(session.config),deepcopy(session.pending),session.sequence,session.transcript.tell())
                assert session.child.poll() is None
                try:session.commit(ready+[bad])
                except ValueError:pass
                else:raise AssertionError('API accepted closed view')
                after=(session.config,session.pending,session.sequence,session.transcript.tell())
                assert before==after and session.child.poll() is None
                rejected=True
            finish=not ready and len(submitted)==len(work['operations']) and event['drained']
            session.commit(ready,finish=finish);submitted.update(o['id'] for o in ready)
    result=json.loads((healthy/'result.json').read_text())
    assert rejected and result['passed'] and result['session']['finished'] and result['completed_operations']==1

    # Deliberately bypass Python. A valid close followed by invalid reuse in ONE
    # COMMIT must fail before either member is registered or executed.
    raw=a.output/'native';raw.mkdir();(raw/'initial.json').write_text(json.dumps(initial)+'\n')
    batch=[work['operations'][0],bad]
    command=dict(type='COMMIT',decision_id=0,now_ns=0,submissions=batch,finish=False)
    argv=[str(a.backend),str(a.topology/'topology.json'),str(a.protocol),str(raw/'initial.json'),str(raw/'result.json')]
    done=subprocess.run(argv,input=(json.dumps(command)+'\n').encode(),capture_output=True,timeout=15,cwd=raw)
    (raw/'stdout.log').write_bytes(done.stdout);(raw/'stderr.log').write_bytes(done.stderr)
    (raw/'command.json').write_text(json.dumps(dict(argv=argv,message=command,exit_code=done.returncode),indent=2)+'\n')
    result=json.loads((raw/'result.json').read_text());t=result['transactions']
    assert done.returncode!=0 and not result['passed'] and 'application use' in result['execution_error']
    assert not result['local_work'] and not t['operations'] and not t['application_windows']['events']
    assert all(not v['closed'] and not v['active_operations'] for v in t['application_windows']['views'])
    assert not t['transport']['source_packets']
    (a.output/'verification.json').write_text(json.dumps(dict(schema_version='application-windows-online/1',
        api_rejection_preserves_live_state=True,api_continuation_completed=True,
        native_rejects_entire_candidate_before_mutation=True,native_expected_exit=done.returncode),indent=2)+'\n')
    print('API continued after rejection; native rejected whole batch without mutation')


if __name__=='__main__':main()
