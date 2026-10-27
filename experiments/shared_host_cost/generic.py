"""Existing generic graph with optional public host observation; no controller internals."""
import argparse
from copy import deepcopy
from pathlib import Path
from moe_ub_cosim.host_timing import HostTiming
from moe_ub_cosim.transaction_session import NativeTransactionSession
from experiments.static_dag_transfer.event_graph import EventGraph
from experiments.shared_uplink_choice.prepare import load, save, sha


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--batch',type=int,choices=(4,8),required=True)
    a=p.parse_args();stage=a.stage.resolve();batch=a.batch
    root=stage/'frozen'/f'batch{batch}'
    assert all(sha(root/n)==digest for n,digest in load(root/'identity.json').items())
    out=stage/'truth'/f'batch{batch}'/'generic';out.mkdir()
    timing=HostTiming(out/'worker_host.jsonl');complete=False;controller=None
    try:
        with timing.span('generic_worker'):
            with timing.span('controller_init'):
                graph,program=load(root/'graph.json'),load(root/'program.json')
                controller=EventGraph(graph,program)
                initial=deepcopy(program);initial['operations']=[]
            try:
                with timing.span('session_init'):
                    session=NativeTransactionSession(stage/'observed/baseline-topology/topology.json',
                        stage/'observed/baseline-protocol.json',initial,stage/'runtime/run-transaction-session',out,
                        timing=timing,callback_batches=True)
                with session:
                    while True:
                        with timing.span('native_receive'):event=session.receive()
                        if event is None:break
                        with timing.span('controller_advance'):nodes=controller.advance(event)
                        finished=not nodes and event['drained'] and controller.snapshot()['all_events_fired']
                        assert nodes or finished or not event['drained'],'generic graph blocked at native drain'
                        with timing.span('wake_conditions'):groups=controller.wake_groups()
                        with timing.span('native_commit'):session.commit(nodes,finish=finished,wake_groups=groups)
            finally:
                if controller is not None:
                    with timing.span('generic_report_write'):save(out/'event-execution.json',controller.snapshot())
        result=load(out/'result.json')
        assert result['passed'] and result['session']['finished'] and controller.snapshot()['all_events_fired']
        assert result['transactions']['transport']['physical_drained'] and result['packet_capture']['omitted_events']==0
        complete=True
    finally:timing.close(complete)


if __name__=='__main__':main()
