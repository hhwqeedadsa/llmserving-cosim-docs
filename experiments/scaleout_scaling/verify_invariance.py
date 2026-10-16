"""Check observer optimization against complete results and failed-run prefix.

Reads public artifacts only. The failed prefix is evidence of unchanged events
up to that point, not evidence that the failed run completed.
"""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path,length=None):
    remaining=length;hasher=hashlib.sha256()
    with path.open('rb') as stream:
        while remaining is None or remaining>0:
            data=stream.read(1048576 if remaining is None else min(1048576,remaining))
            if not data:break
            hasher.update(data)
            if remaining is not None:remaining-=len(data)
    if remaining not in (None,0):raise ValueError('file shorter than expected prefix')
    return hasher.hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pair',nargs=2,type=Path,action='append',default=[],metavar=('BEFORE_RUN','AFTER_RUN'))
    parser.add_argument('--prefix',nargs=2,type=Path,metavar=('FAILED_LOG','COMPLETE_LOG'))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve()
    if output.exists():raise ValueError('use a new evidence output path')
    if not args.pair and not args.prefix:parser.error('at least one comparison required')
    evidence=dict(complete_pairs=[],failed_prefix=None,simulation_executed=False)
    for before,after in args.pair:
        if not json.loads((after/'verification.json').read_text())['passed']:
            raise ValueError('after run is not independently verified')
        a=digest(before/'result.json');b=digest(after/'result.json')
        if a!=b:raise ValueError('result changed: '+str(after))
        old_files={p.relative_to(before/'graphs'):digest(p) for p in (before/'graphs').rglob('*') if p.is_file()}
        new_files={p.relative_to(after/'graphs'):digest(p) for p in (after/'graphs').rglob('*') if p.is_file()}
        if old_files!=new_files:raise ValueError('generated graph changed: '+str(after))
        evidence['complete_pairs'].append(dict(before=str(before),after=str(after),result_sha256=a,
            graph_files=len(old_files),result_and_graphs_byte_identical=True))
    if args.prefix:
        before,after=args.prefix;size=before.stat().st_size
        with before.open('rb') as stream:
            start=max(0,size-65536);stream.seek(start);tail=stream.read()
        end=tail.rfind(b'\n')
        if end<0:raise ValueError('no complete line at failed log tail')
        length=start+end+1
        old=digest(before,length);new=digest(after,length)
        if old!=new:raise ValueError('ASTRA execution differs within the failed-run prefix')
        evidence['failed_prefix']=dict(failed_log=str(before),complete_log=str(after),
            complete_line_prefix_bytes=length,sha256=old,byte_identical=True,
            scope='unchanged actual node events before failure; no completion claim for failed run')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps(evidence,indent=2))


if __name__=='__main__':main()
