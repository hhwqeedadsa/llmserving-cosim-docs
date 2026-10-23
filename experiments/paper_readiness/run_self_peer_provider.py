"""Run the bounded, offline recording-API audit from an existing source snapshot."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--json-include', type=Path, required=True,
                   help='Pinned helper directory containing json/json.hpp')
    a = p.parse_args()
    source, out, include = a.source.resolve(), a.output.resolve(), a.json_include.resolve()
    here = Path(__file__).resolve().parent
    repo = here.parents[1]
    assert source.is_dir() and (include/'json/json.hpp').is_file()
    assert not out.exists() and out.parent.is_dir()
    assert shutil.disk_usage(out.parent).free > 8*2**30+32*2**20
    out.mkdir()
    # Each monitor is a dedicated process; the caller need not set PYTHONPATH.
    env = {**os.environ, 'PYTHONPATH':str(repo/'python'), 'PYTHONDONTWRITEBYTECODE':'1'}
    receipts = []

    def stage(name, command):
        monitor = out/(name+'-monitor')
        cmd = [sys.executable, '-m', 'moe_ub_cosim.host_probe', '--output-dir', str(monitor),
               '--rss-bytes','1073741824','--address-bytes','2147483648',
               '--artifact-bytes','33554432','--wall-seconds','60',
               '--artifact-root',str(out),'--min-free-bytes','8589934592','--', *map(str, command)]
        result = subprocess.run(cmd, env=env, cwd=out, timeout=65, capture_output=True)
        row = json.loads((monitor/'probe.json').read_text())
        receipts.append(dict(stage=name, command=cmd, monitor_exit=result.returncode,
                             monitor_stdout=result.stdout.decode(), monitor_stderr=result.stderr.decode(),
                             report=row))
        (out/'receipts.json').write_text(json.dumps(receipts,indent=2)+'\n')
        assert result.returncode == 0 and row['child_exit_code'] == 0, name
        assert sum(r['report']['wall_seconds'] for r in receipts) < 300

    stage('extract', [sys.executable, here/'self_peer_provider.py', 'extract',
                     '--source',source,'--output',out/'extracted'])
    stage('build', ['g++','-std=c++17','-O1','-Wall','-Wextra','-Werror',
                    '-I',here,'-I',out/'extracted','-I',include,here/'self_peer_driver.cpp',
                    '-o',out/'self-peer-host-probe'])
    stage('execute', [out/'self-peer-host-probe'])
    stage('check', [sys.executable, here/'check_self_peer_provider.py',
                    '--source',source,'--extracted',out/'extracted',
                    '--actual',out/'execute-monitor/stdout.log','--output',out/'verification.json'])
    stage('repeat', [out/'self-peer-host-probe'])
    first = (out/'execute-monitor/stdout.log').read_bytes()
    assert first == (out/'repeat-monitor/stdout.log').read_bytes()
    result = dict(completed=True, receipt_stages=len(receipts), repeat_byte_identical=True,
                  actual_sha256=hashlib.sha256(first).hexdigest(),
                  network_executed=False, vendor_runtime_executed=False)
    (out/'run.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
