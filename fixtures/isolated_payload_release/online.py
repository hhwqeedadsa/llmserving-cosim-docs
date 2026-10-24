"""Public session API: rejected oversized batch, then original valid batch."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from moe_ub_cosim.transaction_session import NativeTransactionSession


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('topology','protocol','work','backend','output'):
        p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();a.output.mkdir()
    work=json.loads(a.work.read_text());initial=deepcopy(work);initial['operations']=[]
    session=NativeTransactionSession(a.topology,a.protocol,initial,a.backend,a.output)
    submitted=False;rejected=False;terminal=None
    try:
        while True:
            try:message=session.receive()
            except ValueError as e:
                if 'native transaction session failed; inspect partial result' not in str(e):raise
                result=json.loads((a.output/'result.json').read_text())
                assert session.child.poll()==1 and result['finish_ns']==5000000 and not result['passed']
                assert 'execution_error' not in result
                terminal='expected_partial_exit_1';break
            if message is None:raise AssertionError('failed connection incorrectly completed session')
            if not submitted:
                extra=deepcopy(work['operations'][2]);extra.update(id=5,address=8256,after=[])
                try:session.commit(work['operations']+[extra])
                except ValueError as e:
                    assert 'retained target request cache capacity exceeded' in str(e)
                    assert session.config['operations']==[]
                    rejected=True
                else:raise AssertionError('oversized batch admitted')
                session.commit(work['operations']);submitted=True
            else:session.commit([])
    finally:session.close()
    assert rejected and submitted and terminal
    (a.output/'driver.json').write_text(json.dumps(dict(rejected_batch_no_partial_admission=True,
        terminal=terminal),indent=2)+'\n')


if __name__=='__main__':main()
