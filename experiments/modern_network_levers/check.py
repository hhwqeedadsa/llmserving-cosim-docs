"""Independent result invariants and bounded evidence tamper rejections."""
import argparse
from copy import deepcopy
from pathlib import Path
from experiments.modern_network_levers.analyze import analyze,audit,load,save


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);a=p.parse_args()
    root=a.stage/'baseline';result=load(root/'result.json');execution=load(root/'modern-execution.json');rejected=[]
    changes={
        'wrong_network_rate':lambda r:r['ports'][0].update(rate_bps=1),
        'wrong_propagation':lambda r:r['ports'][0].update(delay_ns=1),
        'false_drain_time':lambda r:r.update(finish_ns=1),
        'truncated_source_payload':lambda r:r['transactions']['transport']['source_payload_packets'][0].update(hex='00'),
        'duplicate_target_effect':lambda r:r['transactions']['target_events'].append(deepcopy(r['transactions']['target_events'][0])),
        'unacked_prefix':lambda r:r['transactions']['transport']['recovery']['states'][0].update(acknowledged_bytes=0),
    }
    for name,change in changes.items():
        modified=deepcopy(result);change(modified)
        try:audit(root,{'result.json':modified})
        except (AssertionError,KeyError,ValueError):rejected.append(name)
        else:raise AssertionError('tampered evidence admitted: '+name)
    missing=deepcopy(execution);missing['events'].pop()
    try:audit(root,{'modern-execution.json':missing})
    except (AssertionError,KeyError,ValueError):rejected.append('missing_modern_event')
    else:raise AssertionError('missing event admitted')
    summary=analyze(a.stage);save(a.stage/'summary.json',summary)
    for name,case in summary['cases'].items():
        assert case['capture_complete'] and not case['omitted_capture_events']
        assert case['source_packets']==sum(case['source_packet_groups'].values())
        assert not case['repeated_source_packets'] and not case['timeout_expiries']
        assert case['reference_event_kinds'].keys()=={'source_sent','qp_close'}
        assert case['model_application_stage_complete_ns']<=case['full_modern_events_and_source_release_ns']<=case['network_drained_ns']
    save(a.stage/'verification.json',dict(passed=True,cases=3,tamper_rejections=rejected,
        closed_network_executions=3,additional_network_executions=0,
        capture_complete=True,full_physical_path_reconstruction_claimed=False,
        completion_definitions_checked=True,scope='public-output integrity and declared model comparison; no independent hardware accuracy evidence'))


if __name__=='__main__':main()
