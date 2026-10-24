"""Replay a verified local package using a fresh source root and empty cwd."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main(package, output):
    output.mkdir(exist_ok=False)
    empty = output/'empty-cwd'
    empty.mkdir()
    source, evidence = package/'source', package/'evidence'
    environment = dict(os.environ, PYTHONPATH=str(source/'python'), PYTHONDONTWRITEBYTECODE='1')
    records = []

    def run(name, arguments, roots):
        command = [sys.executable, '-B', '-m', 'moe_ub_cosim.host_probe', '--output-dir', str(output/(name+'-monitor')),
                   '--rss-bytes', str(2**30), '--address-bytes', str(2**31), '--artifact-bytes', str(64*2**20),
                   '--wall-seconds', '60', '--min-free-bytes', str(8*2**30)]
        for root in roots:
            command += ['--artifact-root', str(root)]
        command += ['--', sys.executable, '-B', *map(str, arguments)]
        completed = subprocess.run(command, cwd=empty, env=environment, capture_output=True, text=True, timeout=65)
        records.append(dict(command=command, cwd=str(empty), exit=completed.returncode,
                            stdout=completed.stdout, stderr=completed.stderr))
        (output/'commands.json').write_text(json.dumps(records, indent=2)+'\n')
        if completed.returncode != 0:
            raise ValueError('replay failed: '+name)

    fixtures = source/'fixtures/switch_link_failure'
    examples = source/'examples/scaleout_link_failure'
    run('offline', [fixtures/'verify.py', '--stage', evidence, '--examples', examples, '--output', output/'audits'], [output/'audits'])
    run('persistent-offline', [fixtures/'audit_persistent.py', '--stage', evidence, '--examples', examples,
                               '--output', output/'persistent.json'], [output/'persistent.json', output/'persistent-tampering.json'])
    for original, copied in [(evidence/'audit-final/audits.json', output/'audits/audits.json'),
                             (evidence/'audit-final/tampering.json', output/'audits/tampering.json'),
                             (evidence/'persistent-audit-final.json', output/'persistent.json'),
                             (evidence/'persistent-audit-final-tampering.json', output/'persistent-tampering.json')]:
        if original.read_bytes() != copied.read_bytes():
            raise ValueError('offline output changed: '+str(original))
    run('topology', ['-m', 'moe_ub_cosim', 'scaleout', 'topology', '--config', examples/'queued-topology.json',
                      '--output', output/'topology'], [output/'topology'])
    if (output/'topology/topology.json').read_bytes() != (evidence/'queued-topology/topology.json').read_bytes():
        raise ValueError('fresh public topology changed')
    run('network', ['-m', 'moe_ub_cosim', 'scaleout', 'rdma-probe', '--topology', output/'topology',
                     '--config', examples/'queued-rdma.json', '--messages', examples/'messages.json',
                     '--backend', package/'bin/probe', '--output', output/'network'], [output/'network'])
    original = json.loads((evidence/'queued-failover/result.json').read_text())
    fresh = json.loads((output/'network/result.json').read_text())
    loaded = fresh.pop('loaded_libraries')
    original.pop('loaded_libraries')
    if fresh != original:
        raise ValueError('whole native replay result differs beyond library paths')
    native = [Path(p).resolve() for p in loaded if Path(p).name.startswith('libns3')]
    if len(native) != 8 or any(p.parent != (package/'lib').resolve() for p in native):
        raise ValueError('native replay loaded libraries outside verified package')
    manifest = json.loads((package/'manifest.json').read_text())
    for library in native:
        actual = hashlib.sha256(library.read_bytes()).hexdigest()
        if actual != manifest['files']['lib/'+library.name]['sha256']:
            raise ValueError('loaded library hash changed')
    run('live-audit', [fixtures/'audit.py', '--topology', output/'topology/topology.json',
                        '--config', examples/'queued-rdma.json', '--messages', examples/'messages.json',
                        '--result', output/'network/result.json', '--output', output/'live-audit.json'], [output/'live-audit.json'])
    live = json.loads((output/'live-audit.json').read_text())
    if live != json.loads((evidence/'audit-final/audits.json').read_text())['queued-failover']:
        raise ValueError('live packet audit differs')
    result = dict(schema_version='switch-link-replay/1', fresh_python_source_root=True, empty_cwd=True,
                  native_clean_build=False, same_linux_host=True, offline_outputs_byte_identical=4,
                  whole_native_result_equal_except_library_paths=True, eight_libraries_loaded_from_package=True,
                  live_packet_audit_equal=True, package=str(package))
    (output/'replay.json').write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('package', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    main(args.package.resolve(), args.output.resolve())
