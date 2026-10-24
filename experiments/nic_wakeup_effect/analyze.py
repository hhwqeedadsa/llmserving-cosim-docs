"""All application writes and preregistered train; observed/public data only."""
import argparse
from pathlib import Path
from experiments.nic_wakeup_effect.stage import load,save
from experiments.local_fault_baselines.graph_summary import case_summary
from experiments.local_fault_baselines.analyze import difference,first_dispatch
from experiments.local_fault_baselines.mechanism import summarize as mechanism
from experiments.reordering_baseline.train import summarize as train


def main():
    p=argparse.ArgumentParser()
    for k in ('observed','planned','network','baseline','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();cases={};mechanisms={};dispatch={};trains={}
    for version,planned,network in (('old',a.baseline,a.baseline),('fixed',a.planned,a.network)):
        for condition in load(a.observed/'index.json')['cases']:
            name=version+'-'+condition;r=load(network/condition/'result.json')
            cases[name]=case_summary(a.observed,planned/condition,r)
            mechanisms[name]=mechanism(r,cases[name]);dispatch[name]=first_dispatch(r,cases[name])
            if condition!='healthy':trains[name]=train(r)
    pairs=[('old-'+c,'fixed-'+c) for c in ('healthy','live-delayed','live-instant')]
    for v in ('old','fixed'):
        pairs += [(v+'-live-delayed',v+'-live-instant'),(v+'-healthy',v+'-live-delayed'),(v+'-healthy',v+'-live-instant')]
    save(a.output,dict(schema_version='nic-wakeup-effect-analysis/1',cases=cases,mechanisms=mechanisms,
        first_dispatch=dispatch,work99_source_trains=trains,contrasts=[difference(x,y,cases[x],cases[y]) for x,y in pairs],
        scope=load(a.observed/'index.json')['scope'],warning='observed model intervention and exact accounting; no hardware calibration, unique removable components or new algorithm'))


if __name__=='__main__':main()
