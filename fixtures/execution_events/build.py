"""Recompile the header dependency closure into isolated objects/archive.

Uses the existing compile database and Ninja dependency records. Never changes
old outputs. No configure, download, parallel compilation or cold-build claim.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    with path.open('x') as stream:json.dump(value, stream, indent=2);stream.write('\n')


def build(base, output):
    output.mkdir(parents=True, exist_ok=False)
    cache = (base/'CMakeCache.txt').read_text().splitlines()
    ninja = next(line.split('=', 1)[1] for line in cache if line.startswith('CMAKE_MAKE_PROGRAM:'))
    headers = [ROOT/'third_party/LLMServingSim/astra-sim/astra-sim/workload/Workload.hh',
               ROOT/'backend/rdma/astra_adapter.hh']
    deps = subprocess.check_output([ninja, '-C', str(base), '-t', 'deps'], text=True)
    (output/'original-ninja-deps.txt').write_text(deps)
    closure = set()
    for block in deps.split('\n\n'):
        if any('    '+str(header)+'\n' in block for header in headers):
            closure.add(block.split(': #deps')[0])
    assert closure and len(closure) <= 40
    rows = json.loads((base/'compile_commands.json').read_bytes())
    selected = []
    for row in rows:
        argv = shlex.split(row['command'])
        obj = argv[argv.index('-o')+1]
        if obj in closure:
            selected.append((row, obj, argv))
    assert {obj for _, obj, _ in selected} == closure
    old = json.loads((ROOT/'runs/phase_completion_20261030/build-configured/commands.json').read_bytes())
    link = old['link']
    archive = base/'scratch/lib/libAstraSim.a'
    originals = {path.resolve() for path in (base/'../output').resolve().rglob('*') if path.is_file()}
    originals.update((archive, Path(old['link'][old['link'].index('-o')+1])))
    originals.update((base/obj).resolve() for _, obj, _ in selected)
    hashes = {str(path):sha(path) for path in originals}
    save(output/'original-hashes.json', hashes)
    (output/'previous').mkdir()
    shutil.copy2(Path(old['link'][old['link'].index('-o')+1]), output/'previous/service')
    shutil.copy2(archive, output/'previous/libAstraSim.a')
    replacements = {}
    commands = []
    for i, (row, obj, argv) in enumerate(selected):
        target = output/'objects'/f'{i:02d}'/Path(obj).name
        target.parent.mkdir(parents=True)
        argv[argv.index('-o')+1] = str(target)
        commands.append(dict(argv=argv, cwd=row['directory'], source_sha256=sha(Path(row['file'])),
                             previous_object=obj, output=str(target)))
        replacements[obj] = target
    members = subprocess.check_output(['/usr/bin/ar', 't', str(archive)], text=True).splitlines()
    assert max(Counter(members).values()) == 1
    new_archive = output/'libAstraSim.a'
    shutil.copy2(archive, new_archive)
    archive_objects = [path for obj, path in replacements.items() if obj.startswith('scratch/AstraSim/')]
    assert all(Path(obj).name in members for obj in replacements if obj.startswith('scratch/AstraSim/'))
    commands.append(dict(argv=['/usr/bin/ar', 'r', str(new_archive), *map(str, archive_objects)], cwd=str(base)))
    commands.append(dict(argv=['/usr/bin/ranlib', str(new_archive)], cwd=str(base)))
    new_binary = output/'ns3.42-moe-rdma-service'
    link[link.index('-o')+1] = str(new_binary)
    old_service_object = old['compile'][old['compile'].index('-o')+1]
    service_source = ROOT/'backend/rdma/service.cc'
    service_obj = next(obj for row, obj, _ in selected if Path(row['file']) == service_source)
    link[link.index(old_service_object)] = str(replacements[service_obj])
    for i, arg in enumerate(link):
        if arg in replacements:link[i] = str(replacements[arg])
        elif arg == 'scratch/lib/libAstraSim.a':link[i] = str(new_archive)
    commands.append(dict(argv=link, cwd=str(base)))
    save(output/'commands.json', commands)
    for i, row in enumerate(commands):
        with (output/f'{i:02d}.stdout').open('x') as out, (output/f'{i:02d}.stderr').open('x') as err:
            result = subprocess.run(row['argv'], cwd=row['cwd'], stdout=out, stderr=err)
        save(output/f'{i:02d}.status.json', dict(exit_code=result.returncode))
        if result.returncode:raise RuntimeError(f'build step {i} failed; preserve logs')
    assert all(sha(Path(path)) == digest for path, digest in hashes.items())
    save(output/'backend.json', dict(service=str(new_binary), service_sha256=sha(new_binary),
        astra_archive_sha256=sha(new_archive), recompiled_objects=sorted(closure),
        header_sha256={str(p):sha(p) for p in headers}, old_outputs_unchanged=True,
        scope='header dependency closure and isolated link; existing native libraries; not a cold build'))
    print(json.dumps(dict(passed=True, service=str(new_binary), recompiled=len(selected), old_outputs_unchanged=True)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.build_dir.resolve(), args.output.resolve())
