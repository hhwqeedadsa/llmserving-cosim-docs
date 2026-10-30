"""Check independent-build provenance and raw normal behavior before fault runs."""
import argparse
import hashlib
import json
from pathlib import Path


def load(p):
    return json.loads(p.read_bytes())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('study', 'old-build', 'new-build', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    original = a.study/'frozen.json'
    f = load(original if original.exists() else a.study/'frozen-v2.json')
    before, after = a.old_build.resolve(), a.new_build.resolve()
    oldp, newp = load(before/'provenance.json'), load(after/'provenance.json')
    assert oldp['native_commit'] == newp['native_commit']
    assert oldp['serial_build_overlay_sha256'] == newp['serial_build_overlay_sha256']
    compiled = []
    for build in (before, after):
        command_file = build/'cmake/compile_commands.json'
        assert sha(command_file) == load(build/'compiler-options.json')['compile_commands_sha256']
        compiled.append(json.dumps(load(command_file), sort_keys=True).replace(str(build), '<BUILD>'))
    assert compiled[0] == compiled[1]
    assert len(load(after/'cmake/compile_commands.json')) == 442
    old, new = load(Path(f['normal'])/'result.json'), load(a.study/'normal/result.json')
    assert sorted(Path(x).name for x in old['loaded_libraries']) == sorted(Path(x).name for x in new['loaded_libraries'])
    old.pop('loaded_libraries'); new.pop('loaded_libraries')
    assert old == new
    new_hashes = {p.name: sha(p) for p in (after/'output/lib').glob('libns3*.so')}
    old_hashes = {p.name: sha(p) for p in (before/'output/lib').glob('libns3*.so')}
    assert len(new_hashes) == len(old_hashes) == 8
    assert all(new_hashes[Path(p).name] == h for p, h in f['libraries_sha256'].items())
    v = dict(passed=True, native_commit=oldp['native_commit'],
        native_compile_commands_equal_after_build_path_normalization=True, translation_units=442,
        serial_overlay_identical=True, full_service_normal_identical_except_loaded_library_paths=True,
        old_library_hashes=old_hashes, new_library_hashes=new_hashes,
        binary_library_equality_claimed=False,
        original_frozen_sha256=sha(original) if original.exists() else None,
        normal_result_sha256=sha(a.study/'normal/result.json'),
        explanation='Independent build paths change ELF RUNPATH/build-id and __FILE__ strings; native source revision, overlay and normalized compilation commands agree. Entire normal result agrees; no binary-library equivalence claim.')
    with a.output.open('x') as s:
        json.dump(v, s, indent=2); s.write('\n')
    print(json.dumps(dict(passed=True, translation_units=442, normal_behavior_equal=True)))


if __name__ == '__main__':
    main()
