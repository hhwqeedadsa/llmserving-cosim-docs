"""Prepare, run and inspect normal Scale-Up experiments and paired send slots."""
import argparse
import json
from pathlib import Path

from .inputs import prepare
from .slots import compare_slots, prepare_slots
from .workflow import overview, plot, preflight, report, run_batch, run_case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    p = sub.add_parser('prepare',help='Freeze all nine independent experiment inputs; no simulation')
    p.add_argument('--output',type=Path,required=True)
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
    p=sub.add_parser('report',help='Verify and collect all nine case overviews')
    p.add_argument('--batch',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('plot',help='Render a standalone figure from nine verified overviews (requires matplotlib)')
    p.add_argument('--batch',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='prepare': result=prepare(args.output)
    elif args.command=='prepare-slots': result=prepare_slots(args.baseline,args.output)
    elif args.command=='compare-slots': result=compare_slots(args.batch,args.output)
    elif args.command=='preflight': result=preflight(args.batch,args.output)
    elif args.command=='run': result=run_case(args.batch,args.case,args.backend)
    elif args.command=='run-batch': result=run_batch(args.batch,args.backend)
    elif args.command=='plot': result=plot(args.batch,args.output)
    elif args.command=='overview':
        summary=overview(args.run,args.output)
        result=dict(passed=True,output=str(args.output),round_ns=summary['round_ns'])
    else: result=report(args.batch,args.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
