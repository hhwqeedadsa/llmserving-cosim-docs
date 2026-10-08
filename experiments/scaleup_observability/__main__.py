"""Prepare, run and compare Scale-Up executions and isolated observations."""
import argparse
import json
from pathlib import Path

from .inputs import prepare
from .exploration import prepare as prepare_exploration, compare as compare_exploration
from .observation_workflow import prepare_source, study
from .send_state_workflow import prepare_source as prepare_state_source, study as state_study
from .slots import compare_slots, prepare_slots
from .workflow import overview, plot, preflight, report, run_batch, run_case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    p = sub.add_parser('prepare',help='Freeze all nine independent experiment inputs; no simulation')
    p.add_argument('--output',type=Path,required=True)
    p = sub.add_parser('prepare-observation-source',help='Freeze a fresh seed=3 B8 one/two-slot pair; no simulation')
    p.add_argument('--output',type=Path,required=True)
    p = sub.add_parser('prepare-state-source',help='Freeze seed=3 reference and seed=4 holdout slot pairs with actual endpoint state capture')
    p.add_argument('--output',type=Path,required=True)
    for name, flags, description in (
        ('prepare-observations', ('sources',), 'Prepare separate public packages and internal evaluation labels'),
        ('measure-observations', ('observed',), 'Read only an isolated public package and select candidate windows'),
        ('evaluate-observations', ('truth', 'predictions'), 'Evaluate predictions against separately prepared labels'),
        ('prepare-state-observations', ('sources',), 'Prepare three isolated conditions and independent state evaluation labels'),
        ('measure-state-observations', ('observed',), 'Classify sampled actual endpoint counts using only the public package'),
        ('evaluate-state-observations', ('truth','predictions'), 'Evaluate point states and candidate windows for any of the three conditions')):
        p = sub.add_parser(name, help=description)
        for flag in (*flags, 'output'): p.add_argument('--'+flag,type=Path,required=True)
    p = sub.add_parser('prepare-slots',help='Freeze three two-slot inputs against verified one-slot baselines')
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p = sub.add_parser('compare-slots',help='Compare three completed slot pairs and reconstruct SEND occupancy')
    p.add_argument('--batch',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p = sub.add_parser('preflight',help='Check shared cumulative disk reserve for remaining cases')
    p.add_argument('--batch',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    for name,help_text in [('run','Run one declared case'),('run-batch','Serially run declared cases; only verified successes may be reused')]:
        p=sub.add_parser(name,help=help_text)
        p.add_argument('--batch',type=Path,required=True)
        p.add_argument('--backend',type=Path,required=True)
        if name=='run': p.add_argument('--case',required=True)
    p=sub.add_parser('overview',help='Bounded researcher overview of a completed detailed small run')
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--expected-service',type=Path,help='Explicit moe-ub-port-service/1 JSON to match; default requires normal service')
    p=sub.add_parser('report',help='Verify and collect all nine case overviews')
    p.add_argument('--batch',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('plot',help='Render a standalone figure from nine verified overviews (requires matplotlib)')
    p.add_argument('--batch',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('prepare-exploration',help='Freeze a declared small exploration batch without simulation')
    p.add_argument('--study',choices=('compute-tempo','load-grain','port-service'),required=True)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('compare-exploration',help='Check declared graph changes and compare completed exploration cases')
    p.add_argument('--batch',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='prepare': result=prepare(args.output)
    elif args.command=='prepare-exploration': result=prepare_exploration(args.study,args.output)
    elif args.command=='compare-exploration': result=compare_exploration(args.batch,args.output)
    elif args.command=='prepare-observation-source': result=prepare_source(args.output)
    elif args.command=='prepare-state-source': result=prepare_state_source(args.output)
    elif args.command=='prepare-observations': result=study('prepare',[args.sources],args.output)
    elif args.command=='measure-observations': result=study('measure',[args.observed],args.output)
    elif args.command=='evaluate-observations': result=study('evaluate',[args.truth,args.predictions],args.output)
    elif args.command=='prepare-state-observations': result=state_study('prepare',[args.sources],args.output)
    elif args.command=='measure-state-observations': result=state_study('measure',[args.observed],args.output)
    elif args.command=='evaluate-state-observations': result=state_study('evaluate',[args.truth,args.predictions],args.output)
    elif args.command=='prepare-slots': result=prepare_slots(args.baseline,args.output)
    elif args.command=='compare-slots': result=compare_slots(args.batch,args.output)
    elif args.command=='preflight': result=preflight(args.batch,args.output)
    elif args.command=='run': result=run_case(args.batch,args.case,args.backend)
    elif args.command=='run-batch': result=run_batch(args.batch,args.backend)
    elif args.command=='plot': result=plot(args.batch,args.output)
    elif args.command=='overview':
        summary=overview(args.run,args.output,args.expected_service)
        result=dict(passed=True,output=str(args.output),round_ns=summary['round_ns'])
    else: result=report(args.batch,args.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
