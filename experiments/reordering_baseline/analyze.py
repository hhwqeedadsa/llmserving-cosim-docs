"""Observed graph and public network outputs only; no routing truth or verifier."""
import argparse
from pathlib import Path
from experiments.reordering_baseline.stage import load,save
from experiments.local_fault_baselines.graph_summary import case_summary
from experiments.local_fault_baselines.analyze import difference,first_dispatch
from experiments.local_fault_baselines.mechanism import summarize


def main():
    p=argparse.ArgumentParser()
    for k in ('observed','planned','network','baseline','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();cases={};mechanisms={};dispatch={};grace={}
    for condition in load(a.observed/'index.json')['cases']:
        for prefix,directory in (('immediate',a.baseline),('grace',a.planned)):
            name=prefix+'-'+condition;r=load((a.baseline if prefix=='immediate' else a.network)/condition/'result.json')
            summary=case_summary(a.observed,directory/condition,r);cases[name]=summary
            dispatch[name]=first_dispatch(r,summary);mechanisms[name]=summarize(r,summary)
            if prefix=='grace':
                events=r['persistent_transport']['nack_grace']['events']
                grace[name]=dict(events=len(events),arms=sum(e['before']['deadline_ns']<0<=e['after']['deadline_ns'] for e in events),
                    progress_cancellations=sum(e['kind']=='feedback' and e['cumulative_ack']>e['before']['prefix'] and e['before']['deadline_ns']>=0 for e in events),
                    retries=sum(e['kind']=='retry' for e in events),rtos=sum(e['kind']=='rto' for e in events))
    pairs=[('immediate-'+c,'grace-'+c) for c in ('healthy','live-delayed','live-instant')]
    pairs += [('immediate-live-delayed','immediate-live-instant'),('grace-live-delayed','grace-live-instant'),
        ('grace-healthy','grace-live-delayed'),('grace-healthy','grace-live-instant')]
    save(a.output,dict(schema_version='reordering-baseline-analysis/1',cases=cases,mechanisms=mechanisms,first_dispatch=dispatch,
        grace=grace,contrasts=[difference(x,y,cases[x],cases[y]) for x,y in pairs],
        scope=load(a.observed/'index.json')['scope'],
        warning='posthoc delivery and exact queue service are evaluation facts; not source clairvoyance, unique causal components or production gain'))

if __name__=='__main__':main()
