"""One actual remote transaction, zero-duration local work and control alarms."""
import argparse
from copy import deepcopy
from pathlib import Path
from fixtures.native_wakeup.stage import load, save
from fixtures.native_wakeup.verify import delivery
from fixtures.modern_native.audit import memory_audit, wire_audit
from moe_ub_cosim.transaction_session import NativeTransactionSession


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',type=Path,required=True)
    args=parser.parse_args();stage=args.stage.resolve();output=stage/'alarms';output.mkdir()
    initial=load(stage/'original/synthetic-healthy/initial.json')
    previous=load(stage/'original/synthetic-healthy/modern-execution.json')['native_operations']
    connections={c['id']:c for c in initial['connections']}
    remote=deepcopy(next(node for node in previous if node['kind']=='remote' and
                         'self_service' not in connections[node['connection']]))
    remote.update(id=0,after=[])
    local=dict(id=1,kind='local',rank=connections[remote['connection']]['src'],duration_ns=0,
               after=[dict(operation=0,event='applied')])
    nodes=[remote,local];fired=False;rejected=False;sequence=[]
    with NativeTransactionSession(stage/'topology/topology.json',stage/'inputs/synthetic-healthy-protocol.json',
                                 initial,stage/'runtime/run-transaction-session',output) as session:
        while (event:=session.receive()) is not None:
            sequence.append(deepcopy(event))
            if event['decision_id']==0:
                session.commit(nodes,alarms=[dict(id=7,delay_ns=250),dict(id=8,delay_ns=300)],wake_on=[])
            elif any(row.get('alarm')==7 for row in event['events']):
                assert event['now_ns']==250 and not fired;fired=True
                before=(session.sequence,deepcopy(session.config),deepcopy(session._alarms),
                        (output/'control.jsonl').stat().st_size)
                try:
                    session.commit([dict(id=2,kind='local',rank=local['rank'],duration_ns=0,after=[])],
                                   alarms=[dict(id=9,delay_ns=1)],wake_on=[dict(operation=65535,event='completed')])
                except ValueError:
                    rejected=True
                else:raise AssertionError('invalid compound command accepted')
                after=(session.sequence,session.config,session._alarms,(output/'control.jsonl').stat().st_size)
                assert before==after and session.pending is not None
                session.commit([],cancel_alarms=[8],wake_on=[])
            else:
                assert event['drained'] and fired
                session.commit([],finish=True,wake_on=[])
    result=load(output/'result.json');assert result['passed'] and rejected and fired
    assert [a['id'] for a in result['session']['control_alarms']]==[7,8]
    alarm7,alarm8=result['session']['control_alarms']
    assert alarm7['fired_ns']==250 and alarm8['cancelled_ns']==250 and alarm8['fired_ns']==-1
    assert len(result['session']['registrations'])==2 and len(result['local_work'])==1
    node=result['local_work'][0];assert node['duration_ns']==0 and node['start_ns']==node['completed_ns']
    assert result['session']['callback_wakeups']['suppressed_event_batches']>0
    memory=memory_audit(result,initial,{node['id']:node for node in nodes})
    wire=wire_audit(result,initial,{node['id']:node for node in nodes})
    assert len(memory['targets'])==1
    save(stage/'control-verification.json',dict(passed=True,delivery=delivery(output),**wire,
        first_alarm_ns=250,second_alarm_cancelled_ns=250,invalid_compound_command_sent=False,
        remote_target_applications=len(memory['targets']),zero_duration_local_completed=True,
        scope='actual functional remote memory and native control wakeups, not calibrated GPU timing'))


if __name__=='__main__':main()
