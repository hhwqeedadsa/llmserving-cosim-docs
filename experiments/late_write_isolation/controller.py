"""Public callback-only policies; evaluation truth never chooses a submission."""
import argparse
import json
from pathlib import Path
from moe_ub_cosim.transaction_session import NativeTransactionSession


def put(identity,address):
    return dict(id=identity,kind='remote',connection=identity,verb='put',key=9,address=address,
                source=dict(key=7,address=4096,bytes=8),after=[])


class Policy:
    def __init__(self,config):
        self.config=config;self.applied=set();self.deadline=False;self.sent=False;self.copied=False;self.sequence=0
    def decide(self,view):
        assert set(view)=={'decision_id','now_ns','events'} and view['decision_id']==self.sequence
        self.sequence+=1
        for e in view['events']:
            assert e['time_ns']<=view['now_ns']
            if 'alarm' in e:
                assert e['alarm']==99;self.deadline=True
            elif e['event']=='applied':
                assert e['operation'] in (0,1);self.applied.add(e['operation'])
            else:assert e['operation']==2 and e['event']=='completed'
        if view['decision_id']==0:
            assert not view['events'] and view['now_ns']==0
            return [put(0,8192)],[dict(id=99,delay_ns=self.config['deadline_ns'])]
        ops=[]
        if self.deadline and not self.sent and (self.config['mode']!='receive-order' or 0 in self.applied):
            ops.append(put(1,self.config['new_address']));self.sent=True
        if self.config['mode']=='copy-out' and 1 in self.applied and not self.copied:
            ops.append(dict(id=2,kind='local',rank=4,duration_ns=0,
                copy=dict(source=dict(key=9,address=self.config['new_address'],bytes=8),target=dict(key=9,address=self.config['output_address'])),
                after=[dict(operation=1,event='applied')]))
            self.copied=True
        return ops,None


def view(event):
    allowed=[r for r in event['events'] if 'alarm' in r or (r.get('operation') in (0,1) and r['event']=='applied') or (r.get('operation')==2 and r['event']=='completed')]
    return dict(decision_id=event['decision_id'],now_ns=event['now_ns'],events=allowed)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('topology','protocol','initial','policy','backend','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(exist_ok=False);(out/'observed').mkdir();(out/'truth').mkdir()
    config=json.loads(a.policy.read_text());policy=Policy(config);completed=set();terminal=None
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
                if event is None:
                    terminal='native_exit_0';break
                for row in event['events']:
                    if row.get('event')=='completed' and 'operation' in row:completed.add(row['operation'])
                observed=view(event);ops,alarms=policy.decide(observed)
                need={0,1,2} if config['mode']=='copy-out' else {0,1}
                finish=policy.deadline and need<=completed and event['drained']
                history.write(json.dumps(dict(view=observed,submissions=ops,alarms=alarms,finish=finish),sort_keys=True)+'\n')
                session.commit(ops,alarms=alarms,finish=finish)
    finally:
        session.close()
        (out/'observed/terminal.json').write_text(json.dumps(dict(terminal=terminal,native_exit_code=session.child.returncode,policy_new_sent=policy.sent,policy_copy_sent=policy.copied),indent=2)+'\n')
    assert policy.sent and terminal is not None
    print(json.dumps(dict(terminal=terminal,new_sent=policy.sent,mode=config['mode'])))
if __name__=='__main__':main()
