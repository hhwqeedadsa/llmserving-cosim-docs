"""Construct a later payload only from an actual atomic return callback."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from moe_ub_cosim.transaction_session import NativeTransactionSession


def main():
    p=argparse.ArgumentParser()
    for name in ('topology','protocol','work','backend','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();a.output.mkdir(exist_ok=False);w=json.loads(a.work.read_text());initial=dict(w,operations=[])
    program=deepcopy(w['operations'][:-1]);seen=set();submitted=set();returned=None;created=None
    with NativeTransactionSession(a.topology/'topology.json',a.protocol,initial,a.backend,a.output) as session:
        while (event:=session.receive()) is not None:
            for row in event['events']:
                seen.add((row['operation'],row['event']))
                if row['operation']==1 and row['event']=='response':
                    assert returned is None;returned=row['returned_value']
                    marker=deepcopy(w['operations'][-1]);marker['hex']=(100+returned).to_bytes(8,'little').hex()
                    program.append(marker);created=dict(now_ns=event['now_ns'],return_value=returned,marker_hex=marker['hex'])
            ready=[o for o in program if o['id'] not in submitted and all((d['operation'],d['event']) in seen for d in o['after'])]
            finish=event['drained'] and len(submitted)==8 and not ready
            session.commit(ready,finish=finish);submitted.update(o['id'] for o in ready)
    assert returned==17 and created['marker_hex']==(117).to_bytes(8,'little').hex()
    (a.output/'dynamic-construction.json').write_text(json.dumps(dict(passed=True,created=created,
        later_marker_absent_from_initial_native_program=True,actual_callback_only=True),indent=2)+'\n')


if __name__=='__main__':main()
