"""Offline acceptance of a new terminal observation; never runs a network."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from moe_ub_cosim.scaleout_rdma import checked_json, save
from moe_ub_cosim.scaleout_recovery import audit
from moe_ub_cosim.scaleout_switch_drops import analyze
from moe_ub_cosim.scaleout_topology import require


def check(run, reference, output):
    require(not output.exists(), 'diagnosis output already exists')
    current=checked_json(run/'result.json',16777216)
    old=checked_json(reference/'result.json',16777216)
    require(current['recovery']['config'].get('terminal_snapshot') is True,
            'new native run did not enable terminal observation')
    normalized=deepcopy(current)
    del normalized['recovery']['config']['terminal_snapshot']
    for state in normalized['recovery']['states']:
        require('terminal_snapshot' in state, 'missing native terminal observation')
        del state['terminal_snapshot']
    require(sorted(normalized['loaded_libraries'])==sorted(old['loaded_libraries']),
            'loaded library paths differ')
    normalized['loaded_libraries']=old['loaded_libraries']
    require(normalized==old, 'terminal observation changed saved simulation fields')
    recovery=audit(current)
    inputs=checked_json(run/'inputs.json',65536)
    topology=checked_json(Path(inputs['command'][1]),8388608)
    protocol=checked_json(Path(inputs['command'][2]),65536)
    drops=analyze(current,topology,protocol,require_complete=True)
    capture=current['packet_capture'];require(capture['complete'], 'packet record truncated')
    require(current.get('failure',{}).get('reason')=='recovery_retry_exhausted',
            'fixed incast did not preserve retry exhaustion')
    mid=current['failure']['message_id']
    message=next(m for m in current['messages'] if m['id']==mid)
    state=next(s for s in current['recovery']['states'] if s['id']==mid)
    snap=state['terminal_snapshot'];require(snap['qp_present'], 'failed source QP not retained')
    events=capture['events'];source=message['src']
    tx=[e for e in events if e[1]==source and e[3]==0 and e[5]==mid and e[6]==17]
    pfc=[e for e in events if e[1]==source and e[3]==1 and e[6]==254]
    progress=max(e[0] for e in current['recovery']['events'] if e[3]==mid and e[1]==1)
    timeouts=[e[0] for e in current['recovery']['events'] if e[3]==mid and e[1]==2 and e[0]>progress]
    last_tx=max(e[0] for e in tx)
    host=next(h for h in current['rdma_parameters']['hosts'] if h['node']==source)
    summary=dict(passed=True,network_rerun_by_checker=False,all_old_simulation_fields_equal=True,
        original_run_success=current['passed'],failure=current['failure'],recovery_audit=recovery,
        switch_drops=drops,packet_events=len(events),source=source,message_id=mid,
        terminal_snapshot=snap,last_source_data_tx_ns=last_tx,last_ack_progress_ns=progress,
        timeout_requests_after_progress_ns=timeouts,
        actual_data_tx_after_progress=sum(e[0]>progress for e in tx),
        last_source_pfc_rx_ns=max((e[0] for e in pfc),default=None),
        source_ports=[p for p in current['ports'] if p['node']==source],
        native_source_attributes=host['attributes'],
        pacing_deadline_after_stop=snap['next_available_ns']>current['finish_ns'],
        pacing_deadline_minus_stop_ns=snap['next_available_ns']-current['finish_ns'],
        source_last_tx_to_deadline_ns=snap['next_available_ns']-last_tx,
        scope='read-only terminal QP facts and complete packet/timer history; not a new recovery algorithm, production calibration, or gain')
    save(output,summary)
    print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('run','reference','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();check(a.run,a.reference,a.output)
