"""Read-only source chronology and reference-controller summaries."""
import argparse
from pathlib import Path
from experiments.nic_wakeup_effect.stage import load,save


def source(r):
    t=r['persistent_transport'];cols=t['source_packet_columns']
    return [{k:e[k] for k in ('time_ns','stream','sequence','payload_bytes')} for e in (dict(zip(cols,row)) for row in t['source_packets'])]


def main():
    p=argparse.ArgumentParser()
    for k in ('stage','analysis','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();analysis=load(a.analysis);diffs={};rates={}
    for case in ('healthy','live-delayed','live-instant'):
        left=source(load(a.stage/'inputs/baseline'/case/'result.json'));right=source(load(a.stage/'network'/case/'result.json'))
        i=next((i for i,(x,y) in enumerate(zip(left,right)) if x!=y),min(len(left),len(right)))
        diffs[case]=dict(equal_initial_source_events=i,old_first_different=left[i] if i<len(left) else None,
            fixed_first_different=right[i] if i<len(right) else None,total_old=len(left),total_fixed=len(right))
    for name,tr in analysis['work99_source_trains'].items():
        rates[name]=dict(first_rate_bps=tr['first_rate_sample']['rate_bps'],last_rate_bps=tr['last_rate_sample']['rate_bps'],
            reference_cnp_before_first_tx=[e for e in tr['reference_transitions'] if e['kind']=='cnp_received' and e['time_ns']<=tr['first_tx_ns']],
            within_train_rate_updates=[e for e in tr['reference_transitions'] if e['kind'] in ('cnp_received','rp_timer','rp_bytes') and e['time_ns']>tr['first_tx_ns']])
    save(a.output,dict(schema_version='nic-wakeup-effect-chronology/1',first_source_divergence=diffs,work99_reference_rates=rates,
        interpretation='scheduler intervention changes earlier traffic and congestion feedback; rates are declared reference states, not measured hardware; no unique congestion mechanism or removable gain proved',network_executions=0))


if __name__=='__main__':main()
