"""Copy actually received bytes while a separate control deadline is pending.

Run the complete driver under host_probe. The declared 100us deadline is not a
measured failure detector; cancelled deadlines do not consume a GPU queue slot.
"""
import argparse
import json
from pathlib import Path

from moe_ub_cosim.transaction_session import NativeTransactionSession


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('topology','protocol','config','backend','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();program=json.loads(a.config.read_text())
    put,copy=program['operations'];assert put['id']==0 and copy['id']==1
    a.output.mkdir(exist_ok=False)
    pending,copy_submitted,copy_ns=True,False,None
    applied_ns=None
    with NativeTransactionSession(a.topology.resolve()/'topology.json',a.protocol,
            dict(program,operations=[]),a.backend,a.output) as session:
        while (event:=session.receive()) is not None:
            if event['decision_id']==0:
                session.commit([put],alarms=[dict(id=0,delay_ns=100000)])
                continue
            for row in event['events']:
                if row.get('alarm')==0:pending=False
                if row.get('operation')==0 and row['event']=='applied':applied_ns=row['time_ns']
                if row.get('operation')==1 and row['event']=='completed':copy_ns=row['time_ns']
            if applied_ns is not None and not copy_submitted:
                session.commit([copy]);copy_submitted=True
            elif copy_ns is not None and pending:
                session.commit([],finish=event['drained'],cancel_alarms=[0]);pending=False
            else:
                session.commit([],finish=copy_ns is not None and event['drained'] and not pending)
    assert copy_ns is not None and applied_ns<=copy_ns<100000
    summary=dict(receiver_applied_ns=applied_ns,copy_completed_ns=copy_ns,
                 declared_control_deadline_ns=100000,hardware_calibrated=False)
    (a.output/'observed-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))


if __name__=='__main__':main()
