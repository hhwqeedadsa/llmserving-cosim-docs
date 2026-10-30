"""Isolated maintainer build using explicit existing commands; no dependency build."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commands',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--check-source',type=Path,default=ROOT/'fixtures/inflight_helpers/shared_compute_check.cc',
                        help='Explicit standalone FIFO/check source for the second isolated compilation unit')
    args=parser.parse_args();rows=json.loads(args.commands.read_bytes())
    source=str(ROOT/'backend/rdma/service.cc')
    compile_row,=[r for r in rows if source in r['argv'] and '-c' in r['argv']]
    old_obj=compile_row['argv'][compile_row['argv'].index('-o')+1]
    link_row,=[r for r in rows if old_obj in r['argv'] and '-c' not in r['argv']]
    old_binary=link_row['argv'][link_row['argv'].index('-o')+1]
    originals={path:sha(path) for path in [old_obj,old_binary,*[a for a in link_row['argv'] if a.endswith('.a')]]}
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    commands=[]
    for name,src in [('service',source),('shared-compute-check',str(args.check_source.resolve()))]:
        compiler=list(compile_row['argv']);compiler[compiler.index('-o')+1]=str(output/(name+'.o'))
        compiler[compiler.index(source)]=src
        linker=list(link_row['argv']);linker[linker.index(old_obj)]=str(output/(name+'.o'))
        linker[linker.index('-o')+1]=str(output/('ns3.42-moe-rdma-service' if name=='service' else name))
        commands.extend([dict(argv=compiler,cwd=compile_row['cwd']),dict(argv=linker,cwd=link_row['cwd'])])
    save(output/'commands.json',commands);save(output/'original-hashes.json',originals)
    for i,row in enumerate(commands):
        with (output/f'{i}.stdout').open('x') as out,(output/f'{i}.stderr').open('x') as err:
            r=subprocess.run(row['argv'],cwd=row['cwd'],stdout=out,stderr=err)
        save(output/f'{i}.status.json',dict(exit_code=r.returncode))
        if r.returncode:raise RuntimeError(f'build step {i} failed; preserve logs')
    assert all(sha(path)==value for path,value in originals.items())
    save(output/'backend.json',dict(passed=True,service_sha256=sha(output/'ns3.42-moe-rdma-service'),
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'backend/rdma').glob('*') if p.suffix in ('.cc','.hh')},
        old_outputs_unchanged=True,new_objects=2,dependency_builds=0,
        scope='isolated service and CPU shared-FIFO harness using saved commands; not a cold build'))
    print(json.dumps(dict(passed=True,new_objects=2,old_outputs_unchanged=True)))


if __name__=='__main__':main()
