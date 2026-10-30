"""Check the analysis against changed dependencies and source identities."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

spec=importlib.util.spec_from_file_location('completion_analysis',Path(__file__).resolve().parents[1]/'analysis/analyze.py')
analysis=importlib.util.module_from_spec(spec);spec.loader.exec_module(analysis)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('helper','report','sources','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    case=args.helper.resolve();expected=analysis.load(args.report.resolve())['cases']['helper']
    assert analysis.analyze(case)==expected
    copied=output/'changed-run';shutil.copytree(case,copied)
    files=[copied/'result.json',copied/'graphs/g0.w0/wave.json'];originals={p:p.read_bytes() for p in files}
    rejected=[]
    for change,reason in [('missing-recv-edge','original Combine receive omitted'),
        ('early-gate','phase release differs'),('helper-in-original-graph','helper message is in the original graph')]:
        for p,raw in originals.items():p.write_bytes(raw)
        if change=='early-gate':
            r=analysis.load(files[0]);r['service']['phase_completion']['gates'][0]['released_ns']-=1
            files[0].write_text(json.dumps(r))
        else:
            w=analysis.load(files[1]);r=analysis.load(files[0]);helper=r['service']['inflight_helpers']['attempts'][0]['candidate']
            rows=w['rank_nodes'][str(helper['source'])]
            recv=next(n for n in rows if n['kind']=='RECV' and n['message']['id']==helper['combine_message'])
            if change=='missing-recv-edge':
                merge=next(n for n in rows if n['name']=='L0.merge');merge['deps'].remove(recv['id'])
            else:recv['message']['id']=helper['result_message']
            files[1].write_text(json.dumps(w))
        try:analysis.analyze(copied)
        except AssertionError as error:
            assert reason in str(error),str(error);rejected.append(change)
        else:raise AssertionError('changed analysis input accepted: '+change)
    for p,raw in originals.items():p.write_bytes(raw)
    source_copy=output/'source-bundle';shutil.copytree(args.sources.resolve(),source_copy)
    manifest=analysis.load(source_copy/'sources.json');source=source_copy/'sources'/manifest[0]['path']
    source.write_bytes(source.read_bytes()+b'\n')
    try:analysis.sources(source_copy,source_copy)
    except AssertionError:rejected.append('changed-source-sha')
    else:raise AssertionError('changed source accepted')
    assert len(rejected)==4
    analysis.save(output/'verification.json',dict(passed=True,unchanged_analysis_equal=True,rejected=rejected,
        source_analysis_sha256=hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest(),
        new_network_executions=0,scope='analysis guard checks; not new network or application gain'))
    print(json.dumps(dict(passed=True,negative_checks=4,new_network_executions=0)))


if __name__=='__main__':main()
