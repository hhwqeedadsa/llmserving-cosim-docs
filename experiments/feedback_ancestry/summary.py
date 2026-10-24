"""Readable statistics from fully checked packet ancestry; no counterfactuals."""
import argparse
from pathlib import Path
from experiments.feedback_ancestry.stage import load,save


def main():
    p=argparse.ArgumentParser()
    for k in ('analysis','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();data=load(a.analysis);cases={}
    for name,c in data['cases'].items():
        marks={m['packet_uid']:m for m in c['marked_packets']};notices=c['notices'];critical={}
        for wid,w in c['critical_works'].items():
            n=w['last_prior_cnp'];row={k:v for k,v in w.items() if k not in ('last_prior_cnp','prior_cnp_uids')}
            row['prior_cnp_count']=len(w['prior_cnp_uids'])
            if n:
                origins={(x['work'],x['graph_id'],x['name']) for uid in n['np_members'] for x in marks[uid]['work_intersections']}
                row['last_prior_notice']=dict(uid=n['packet_uid'],batch=n['np_batch'],generated_ns=n['generated_ns'],received_ns=n['received_ns'],
                    before_work_ready_ns=w['ready_ns']-n['received_ns'],members=len(n['np_members']),
                    work_origins=[dict(work=i,graph_id=g,name=label) for i,g,label in sorted(origins)],
                    rate_before_bps=n['rate_before_bps'],rate_after_bps=n['rate_after_bps'],
                    all_marked_bytes_acked=n['all_marked_bytes_acked_at_receive'],active_works=n['active_published_works'])
            critical[wid]=row
        by_kind={}
        for kind in ('immediate','periodic'):
            selected=[n for n in notices if n['np_batch']==kind]
            by_kind[kind]=dict(notices=len(selected),all_marked_bytes_acked=sum(n['all_marked_bytes_acked_at_receive'] for n in selected),
                all_marked_works_acked=sum(n['all_marked_works_acked_at_receive'] for n in selected),
                source_no_outstanding_bytes=sum(n['source_ack_prefix']==n['source_published_bytes'] for n in selected))
        sites={}
        for m in marks.values():
            s=m['first_ce'];key=str(s['node'])+':'+str(s['port']);row=sites.setdefault(key,dict(marked_packets=0,with_other_streams_queued=0,more_other_than_own_stream_bytes=0))
            row['marked_packets']+=1;own=s['remaining_by_stream_model_bytes'].get(str(m['stream']),0);other=s['remaining_data_model_bytes']-own
            row['with_other_streams_queued']+=other>0;row['more_other_than_own_stream_bytes']+=other>own
        cases[name]=dict(application_completion_ns=c['application_completion_ns'],statistics=c['statistics'],by_np_kind=by_kind,first_ce_sites=sites,
            critical_works=critical,cnp_reverse_queue_wait_ns=sum(h['wait_ns'] for n in notices for h in n['hops']),
            cnp_physical_hops=sum(len(n['hops']) for n in notices))
    totals={k:sum(c['statistics'][k] for c in cases.values()) for k in next(iter(cases.values()))['statistics']}
    save(a.output,dict(schema_version='feedback-ancestry-summary/1',cases=cases,totals=totals,new_network_executions=0,
        inference='delivery confirmation and congestion feedback have distinct meanings; a completed marked packet/work does not invalidate QP congestion feedback',
        excluded_claims=['unsafe CNP discard','unique counterfactual queue contribution','hardware calibration','modern GIN equivalence','paper novelty']))


if __name__=='__main__':main()
