"""Independent small complete capture/replay check using synthetic inputs."""
import argparse
from pathlib import Path
import sys

from .check_small import check
from ..analysis.path_replay import equivalent, prepare
from ..analysis.workflow import cli, host, read, save


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--backend',type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if not a.worker:
        from moe_ub_cosim.host_probe import probe
        report=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],
            str(a.output.resolve())+'-resources',**host(extra_roots=[a.output]))
        if report['status']!='completed':raise SystemExit('Small delivery flow failed: '+report['status'])
        return
    output=a.output.resolve();backend=a.backend.resolve()
    check(output,backend,complete_coverage=True)
    case=output/'restored'; detail=case/'detail'
    prepare(case,case/'analysis/summary.json',backend,detail)
    cli(detail,'replay','replay','--slice',case/'slice','--config',detail/'replay.json','--output',detail/'run')
    equivalent(case,detail)
    from ..analysis.path_analysis import analyze
    analyze(detail,output/'path-analysis')
    save(output/'delivery-summary.json',dict(passed=True,synthetic_only=True,
        full_workflow=read(output/'summary.json'),equivalence=read(detail/'equivalence.json'),
        paths=read(output/'path-analysis/summary.json')))


if __name__=='__main__':main()
