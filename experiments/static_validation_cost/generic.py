"""Study-owned generic executor: public session, actual callbacks, no timing trace."""
import argparse
from copy import deepcopy
from pathlib import Path

from moe_ub_cosim.host_timing import HostTiming
from moe_ub_cosim.transaction_session import NativeTransactionSession
from experiments.static_dag_transfer.event_graph import EventGraph
from .data import load, save, sha


def run(stage, batch, output, *, cached=False, timing_enabled=False):
    assert not output.exists(); output.mkdir()
    frozen = stage/'frozen'/f'batch{batch}'
    assert all(sha(frozen/n) == digest for n, digest in load(frozen/'identity.json').items())
    timing = HostTiming(output/'worker_host.jsonl' if timing_enabled else None)
    complete = False; controller = None
    try:
        with timing.span('generic_worker'):
            with timing.span('controller_init'):
                program = load(frozen/'program.json'); controller = EventGraph(load(frozen/'graph.json'), program)
                initial = deepcopy(program); initial['operations'] = []
            try:
                with timing.span('session_init'):
                    session = NativeTransactionSession(stage/'observed/baseline-topology/topology.json',
                        stage/'observed/baseline-protocol.json', initial, stage/'runtime/run-transaction-session', output,
                        callback_batches=True, timing=timing, reuse_static_validation=cached)
                with session:
                    while True:
                        with timing.span('native_receive'): event = session.receive()
                        if event is None: break
                        with timing.span('controller_advance'): nodes = controller.advance(event)
                        finished = not nodes and event['drained'] and controller.snapshot()['all_events_fired']
                        assert nodes or finished or not event['drained'], 'generic graph blocked'
                        with timing.span('wake_conditions'): groups = controller.wake_groups()
                        with timing.span('native_commit'): session.commit(nodes, finish=finished, wake_groups=groups)
            finally:
                if controller is not None:
                    with timing.span('generic_report_write'): save(output/'event-execution.json', controller.snapshot())
        result = load(output/'result.json')
        assert result['passed'] and result['session']['finished'] and controller.snapshot()['all_events_fired']
        assert result['transactions']['transport']['physical_drained'] and result['packet_capture']['omitted_events'] == 0
        complete = True
    finally: timing.close(complete)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True); p.add_argument('--batch', type=int, choices=(4, 8), required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cached', action='store_true'); p.add_argument('--host-timing', action='store_true')
    a = p.parse_args(); run(a.stage.resolve(), a.batch, a.output.resolve(), cached=a.cached, timing_enabled=a.host_timing)
