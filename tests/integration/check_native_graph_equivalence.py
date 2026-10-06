"""Compare complete native graph files across builder implementations."""
import argparse
import json
from pathlib import Path

from moe_ub_cosim.plan_io import file_sha256


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--candidate',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    left,right=a.baseline.resolve(),a.candidate.resolve()
    files=lambda root:{str(f.relative_to(root)):f for f in root.rglob('*') if f.is_file()}
    old,new=files(left),files(right);assert set(old)==set(new)
    equal={};location_metadata=[]
    for name in sorted(old):
        if name=='schema/compiler.json':
            before=json.loads(old[name].read_text());after=json.loads(new[name].read_text())
            before['argv']=[s.replace(str(left),'<graph>') for s in before['argv']]
            after['argv']=[s.replace(str(right),'<graph>') for s in after['argv']]
            assert before==after;location_metadata.append(name)
        elif name.startswith('schema/__pycache__/') and name.endswith('.pyc'):
            # CPython embeds the generated module's absolute filename and source
            # mtime. The compiler's exact generated Python source is checked below.
            assert 'schema/et_def_pb2.py' in old;location_metadata.append(name)
        else:
            digest=file_sha256(old[name]);assert digest==file_sha256(new[name]),name
            equal[name]=dict(bytes=old[name].stat().st_size,sha256=digest)
    assert 'plan.json' in equal and len([n for n in equal if n.startswith('layer.') and n.endswith('.et')])==128
    a.output.write_text(json.dumps(dict(passed=True,baseline=str(left),candidate=str(right),
        exact_files=equal,location_dependent_compiler_metadata=location_metadata),indent=2)+'\n')
    print('PASS exact plan, all 128 Chakra graphs, generation hashes and generated schema source')


if __name__=='__main__':main()
