"""Public-CLI post-audit, independent repeat and corrupt-evidence controls."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
def read(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',type=Path,required=True)
    p.add_argument('--backend-json',type=Path,required=True);p.add_argument('--protoc',required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    out=args.output.resolve();out.mkdir(exist_ok=False);(out/'logs').mkdir();records=[]
    def cli(name,argv,passed=True):
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,argv)]
        with (out/'logs'/f'{name}.out').open('w') as stdout,(out/'logs'/f'{name}.err').open('w') as stderr:
            result=subprocess.run(command,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=90)
        records.append(dict(name=name,argv=command,cwd=str(ROOT),exit_code=result.returncode,expected_success=passed))
        save(out/'commands.json',records)
        if (result.returncode==0)!=passed:raise RuntimeError('unexpected audit result: '+name)
        if passed:
            text=(out/'logs'/f'{name}.out').read_text()
            return json.loads(text[text.index('{'):])
        return (out/'logs'/f'{name}.err').read_text()
    rows=read(args.runs/'summary.json');audits=[];zero_count=0
    for row in rows:
        directory=args.runs/f'{row["condition"]}-{row["route_seed"]}'
        audits.append(cli(f'check-{row["condition"]}-{row["route_seed"]}',['rdma-check','--run',directory]))
        for path in (directory/'graphs').glob('*/wave.json'):
            zero_count+=sum(t['kind']=='COMP' and t['duration_ns']==0 for tasks in read(path)['rank_nodes'].values() for t in tasks)
    backend=read(args.backend_json);base=args.runs/'clos-11'
    def service(name,config):
        path=out/f'{name}.json';save(path,config)
        return cli(name,['rdma-run','--config',path,'--topology',base,'--protocol',base/'rdma.json',
            '--backend',backend['service'],'--protoc',args.protoc,'--output',out/name])
    service('repeat',read(base/'input.json'))
    assert sha(base/'result.json')==sha(out/'repeat/result.json'), 'native result differs after independent repeat'
    graph_files=sorted(p.relative_to(base) for p in (base/'graphs').rglob('*') if p.is_file())
    assert all(sha(base/f)==sha(out/'repeat'/f) for f in graph_files), 'Chakra/semantic graph changed'
    modified=read(base/'input.json');modified['model'].update(name='synthetic-H512-E8',hidden_size=512,ffn_hidden_size=2048,experts=8)
    changed_model=service('changed-model',modified)
    corrupt=out/'corrupt-copy';shutil.copytree(base,corrupt)
    result_path=corrupt/'result.json';original=result_path.read_bytes()
    forged=read(result_path)
    next(p for p in forged['ports'] if 'ecn_kmin_bytes' in p)['ecn_kmin_bytes']+=1
    save(result_path,forged);ecn_error=cli('reject-ecn',['rdma-check','--run',corrupt],False)
    assert 'ECN thresholds' in ecn_error
    result_path.write_bytes(original);forged=read(result_path)
    forged['loaded_libraries'].append('/other-build/libns3.42-core.so');save(result_path,forged)
    library_error=cli('reject-mixed-libraries',['rdma-check','--run',corrupt],False)
    assert 'mixed or UB' in library_error
    result_path.write_bytes(original);wave_path=corrupt/'graphs/g0.w0/wave.json';wave=read(wave_path)
    next(n for n in wave['rank_nodes']['0'] if n['kind']=='COMP')['duration_ns']+=1;save(wave_path,wave)
    dependency_error=cli('reject-duration',['rdma-check','--run',corrupt],False)
    assert 'compute duration' in dependency_error
    save(out/'validation.json',dict(passed=True,main_and_rail_checks=len(audits),
        verified_nodes=sum(x['verified_nodes'] for x in audits),zero_duration_nodes_in_main_matrix=zero_count,
        repeated_result_byte_identical=True,result_sha256=sha(base/'result.json'),
        repeated_graph_files_byte_identical=len(graph_files),changed_model=changed_model,
        rejected_corruptions=['ECN effective byte threshold','mixed loaded ns-3 builds','declared compute duration'],
        script_sha256=sha(Path(__file__))))


if __name__=='__main__':main()
