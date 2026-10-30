"""Relocate a saved run and reject corrupt whole-result evidence; no networks."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from moe_ub_cosim.scaleout_verify import verify


def load(path):return json.loads(path.read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def check(run,output):
    output.mkdir(parents=True,exist_ok=False)
    before={str(p.relative_to(run)):sha(p) for p in run.rglob('*') if p.is_file()}
    moved=output/'relocated';shutil.copytree(run,moved)
    clean=verify(moved)
    paths=[moved/name for name in ('frontend.json','whole_call_report.json','whole_call_events.jsonl','execution_events.jsonl')]
    config=load(moved/'input.json');manifest=load(moved/config['route_replay']['manifest']['path'])
    source=moved/manifest['files'][0]['file']['path'];paths.append(source)
    originals={p:p.read_bytes() for p in paths};rejected=[]
    try:
        for name in ('early_result','wrong_winner','missing_loser','foreign_graph','source_identity',
                     'foreign_source_sequence','missing_lifecycle','forged_graph_retirement','raw_source_change'):
            f=load(moved/'frontend.json');report=deepcopy(f['whole_call_replication'])
            call=next(iter(report['calls'].values()))
            if name=='early_result':
                call['result_ready_ns']-=1;report['result_ready_ns']-=1
            elif name=='wrong_winner':call['winner']='replica' if call['winner']=='primary' else 'primary'
            elif name=='missing_loser':
                loser='replica' if call['winner']=='primary' else 'primary'
                call['attempts'][loser]['finished_ns']=None;report['all_attempts_retired']=False
            elif name=='foreign_graph':call['attempts']['primary']['graph_id']='g0.w999'
            elif name=='source_identity':call['input_identity']['sha256']='0'*64
            if name in ('early_result','wrong_winner','missing_loser','foreign_graph','source_identity'):
                f['whole_call_replication']=report
                (moved/'frontend.json').write_text(json.dumps(f))
                (moved/'whole_call_report.json').write_text(json.dumps(report))
            elif name in ('foreign_source_sequence','missing_lifecycle'):
                events=[json.loads(line) for line in (moved/'whole_call_events.jsonl').read_text().splitlines()]
                if name=='foreign_source_sequence':next(e for e in events if e['kind']=='client_result_ready')['execution_sequence']+=1
                else:events=[e for e in events if e['kind']!='client_result_ready']
                (moved/'whole_call_events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
            elif name=='forged_graph_retirement':
                events=[json.loads(line) for line in (moved/'execution_events.jsonl').read_text().splitlines()]
                next(e for e in events if e['kind']=='graph_retired')['time_ns']-=1
                (moved/'execution_events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
            else:source.write_bytes(originals[source]+b' ')
            changed={str(p.relative_to(moved)):sha(p) for p in paths if p.read_bytes()!=originals[p]}
            try:verify(moved)
            except ValueError as error:rejected.append(dict(case=name,error=str(error),changed_sha256=changed))
            else:raise AssertionError('corrupt evidence accepted: '+name)
            finally:
                for p,raw in originals.items():
                    if p.read_bytes()!=raw:p.write_bytes(raw)
        assert len(rejected)==9
        assert before=={str(p.relative_to(run)):sha(p) for p in run.rglob('*') if p.is_file()}
        assert before=={str(p.relative_to(moved)):sha(p) for p in moved.rglob('*') if p.is_file()}
        result=dict(passed=True,new_network_executions=0,relocated_public_check=clean,
                    rejected_mutations=rejected,original_files_unchanged=True,restored_copy_identical=True)
        (output/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(dict(passed=True,rejected=len(rejected),relocated=True)))
    finally:
        for p,raw in originals.items():
            if p.read_bytes()!=raw:p.write_bytes(raw)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();check(args.run.resolve(),args.output.resolve())
