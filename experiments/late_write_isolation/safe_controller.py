"""Callback-only amendment: disjoint output or explicit remote effect order."""
import argparse
import json
from pathlib import Path
from controller import Policy,view
from moe_ub_cosim.transaction_session import NativeTransactionSession


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('topology','protocol','initial','policy','backend','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(exist_ok=False);(out/'observed').mkdir();(out/'truth').mkdir()
    config=json.loads(a.policy.read_text());assert config['mode'] in ('isolated','receive-order')
    policy=Policy(config);completed=set();terminal=None
    session=NativeTransactionSession(a.topology,a.protocol,json.loads(a.initial.read_text()),a.backend,out/'truth')
    try:
        with (out/'observed/decisions.jsonl').open('x',buffering=1) as history:
            while True:
                try:event=session.receive()
                except ValueError as error:
                    if 'native transaction session failed; inspect partial result and native.stderr.log' not in str(error):raise
                    if session.child.poll()!=1 or not (out/'truth/result.json').exists():raise
                    result=json.loads((out/'truth/result.json').read_text())
                    assert not result['passed'] and result['finish_ns']==5000000 and 'execution_error' not in result
                    terminal='native_partial_exit_1';break
                if event is None:terminal='native_exit_0';break
                for e in event['events']:
                    if e.get('event')=='completed' and 'operation' in e:completed.add(e['operation'])
                observed=view(event);ops,alarms=policy.decide(observed)
                if config['mode']=='receive-order':
                    for op in ops:
                        if op['id']==1:op['after']=[dict(operation=0,event='applied')]
                finish=policy.deadline and {0,1}<=completed and event['drained']
                history.write(json.dumps(dict(view=observed,submissions=ops,alarms=alarms,finish=finish),sort_keys=True)+'\n')
                session.commit(ops,alarms=alarms,finish=finish)
    finally:
        session.close();(out/'observed/terminal.json').write_text(json.dumps(dict(terminal=terminal,native_exit_code=session.child.returncode,policy_new_sent=policy.sent,policy_copy_sent=policy.copied),indent=2)+'\n')
    assert policy.sent and terminal=='native_partial_exit_1'
    print(json.dumps(dict(mode=config['mode'],terminal=terminal)))


if __name__=='__main__':main()
