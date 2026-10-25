"""Summarize observed stress results without claiming missing packet coverage."""
import argparse
from pathlib import Path
from experiments.extreme_fault_stress.stage import load, save


def analyze(stage, inputs):
    c=load(inputs/'config.json'); rows=[]
    for case in c['cases']:
        path=stage/'network'/case['name']/'result.json'
        if not path.exists():
            rows.append(dict(name=case['name'],status='not_run',expected_complete=case['expected_complete']));continue
        r=load(path);t=r['persistent_transport'];pc=r['packet_capture'];w=load(inputs/'observed'/(case['load']+'-work.json'))
        done={x['id']:x for x in r['local_work']};last=done.get(w['work'][-1]['id'],{}).get('completed_ns',-1)
        source_bytes=sum(p[6] for p in t['source_packets'])
        unique=sum(x['bytes'] for x in w['work'] if x['kind']=='write')
        rows.append(dict(name=case['name'],status='complete' if r['passed'] else 'failed',application_completion_ns=last,
            physical_finish_ns=r['finish_ns'],completed_work=r['completed_work'],expected_work=len(w['work']),
            unique_payload_bytes=unique,source_payload_bytes=source_bytes,repeated_payload_bytes=source_bytes-unique if r['passed'] else None,
            timeout_expiries=sum(x['timeout_expiries'] for x in t['recovery']['states']),
            capture_complete=pc['complete'],capture_saved=len(pc['events']),capture_observations=pc['observations'],capture_omitted=pc['omitted_events'],
            recovery_records_complete=t['recovery']['omitted_events']==0,
            source_log_matches_stream_totals=source_bytes==sum(x['source_payload_bytes'] for x in t['streams']),
            application_checked_separately=True,full_packet_path_audited=False))
    by={r['name']:r for r in rows};effects={}
    for name, before, after in [('hotspot_effect','uniform-healthy','hotspot-healthy'),
                              ('fault_effect','hotspot-healthy','hotspot-uplink'),
                              ('combined_effect','uniform-healthy','hotspot-uplink')]:
        if by[before]['status']==by[after]['status']=='complete':
            a,b=(by[x]['application_completion_ns'] for x in (before,after))
            effects[name]=dict(before=before,after=after,delta_ns=b-a,slowdown_percent=(b/a-1)*100,
                               fraction_of_after_percent=(b-a)/b*100)
    return dict(schema='moe-extreme-fault-results/1',synthetic_stress=True,cases=rows,effects=effects,
        actual_networks=sum(x['status']!='not_run' for x in rows),planned_networks=6,phase_closed=True,
        stop_reason='packet_capture_limit_at_hotspot_uplink',successful_network_reruns=0,
        scope='synthetic Top1 role-separated dispatch/expert-stage/combine, declared compute; not modern EP implementation, real routing distribution, GPU or calibrated inference',
        candidate_optimization_gain_measured=False,remaining_fault_gap_is_optimization_bound=False)


def main():
    p=argparse.ArgumentParser()
    for k in ('stage','inputs','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();d=analyze(a.stage.resolve(),a.inputs.resolve());save(a.output,d)
    for r in d['cases']:print(r['name'],r['status'],r.get('application_completion_ns'))


if __name__=='__main__':main()
