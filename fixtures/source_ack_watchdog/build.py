"""Recompile only the service against an explicitly selected saved link receipt.

Maintainer verification helper. Ordinary clean builds use scaleout rdma-build.
No old object, archive or binary is changed; no dependency builds or downloads.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path, value):
    with path.open('x') as stream:json.dump(value, stream, indent=2);stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commands', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = json.loads(args.commands.read_bytes())
    source = ROOT/'backend/rdma/service.cc'
    selected = [r for r in rows if str(source) in r['argv'] and '-c' in r['argv']]
    assert len(selected) == 1
    compile_row = selected[0]
    link_row = rows[-1]
    old_obj = compile_row['argv'][compile_row['argv'].index('-o')+1]
    assert old_obj in link_row['argv'] and '-c' not in link_row['argv']
    old_binary = Path(link_row['argv'][link_row['argv'].index('-o')+1])
    originals = {path: sha(Path(path)) for path in [str(old_binary), old_obj,
        *[arg for arg in link_row['argv'] if arg.endswith('.a') and Path(arg).is_file()]]}
    output = args.output.resolve();output.mkdir(parents=True, exist_ok=False)
    compile_args = list(compile_row['argv'])
    compile_args[compile_args.index('-o')+1] = str(output/'service.cc.o')
    link_args = list(link_row['argv'])
    link_args[link_args.index('-o')+1] = str(output/'ns3.42-moe-rdma-service')
    link_args[link_args.index(old_obj)] = str(output/'service.cc.o')
    commands = [dict(argv=compile_args, cwd=compile_row['cwd']), dict(argv=link_args, cwd=link_row['cwd'])]
    save(output/'commands.json', commands)
    save(output/'original-hashes.json', originals)
    for i, row in enumerate(commands):
        with (output/f'{i}.stdout').open('x') as out, (output/f'{i}.stderr').open('x') as err:
            result = subprocess.run(row['argv'], cwd=row['cwd'], stdout=out, stderr=err)
        save(output/f'{i}.status.json', dict(exit_code=result.returncode))
        if result.returncode:raise RuntimeError(f'build step {i} failed; preserve logs')
    assert all(sha(Path(path)) == value for path, value in originals.items())
    save(output/'backend.json', dict(passed=True, service_sha256=sha(output/'ns3.42-moe-rdma-service'),
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'backend/rdma').glob('*') if p.suffix in ('.cc', '.hh')},
        old_outputs_unchanged=True, new_objects=1, dependency_builds=0,
        scope='isolated service object/link using an explicit previous receipt; not a cold build'))
    print(json.dumps(dict(passed=True, new_objects=1, old_outputs_unchanged=True)))


if __name__ == '__main__':main()
