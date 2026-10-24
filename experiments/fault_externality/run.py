"""Four public CLI conditions, stop at first failure; never overwrite or retry."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time


def load(p): return json.loads(p.read_text())
def save(p, x): p.write_text(json.dumps(x, ensure_ascii=False, indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def allocated(p): return sum(q.stat().st_blocks*512 for q in p.rglob('*') if q.is_file())


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--observed', type=Path, required=True)
    p.add_argument('--backend', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    observed, backend, out = (x.resolve() for x in (a.observed, a.backend, a.output))
    assert backend.is_file() and not out.exists()
    index = load(observed/'index.json')
    for name, digest in index['files'].items(): assert sha(observed/name)==digest
    out.mkdir(); commands=[]; started=time.monotonic()
    identity=dict(backend=str(backend), sha256=sha(backend),
        libraries={str(q):sha(q) for q in sorted((backend.parent.parent/'lib').glob('*.so'))})
    save(out/'identity.json', identity)
    try:
        for label, tail in [('topology', ['topology', '--config', observed/'topology.json', '--output', out/'topology'])] + [
            (c['name'], ['rdma-stream-probe', '--topology', out/'topology', '--protocol', observed/c['protocol'],
                        '--config', observed/c['work'], '--backend', backend, '--output', out/c['name']])
            for c in index['cases']]:
            assert allocated(out.parent)<128*2**20 and time.monotonic()-started<180
            assert shutil.disk_usage(out).free>8*2**30
            cmd=[sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', *map(str,tail)]
            row=dict(label=label, command=cmd, cwd=str(Path.cwd())); commands.append(row)
            start=time.monotonic()
            try:
                with (out/(label+'.stdout.log')).open('w') as stdout, (out/(label+'.stderr.log')).open('w') as stderr:
                    child=subprocess.run(cmd, stdout=stdout, stderr=stderr, timeout=60)
                row['exit_code']=child.returncode
            finally:
                row['wall_seconds']=time.monotonic()-start; save(out/'commands.json', commands)
            assert child.returncode==0, f'{label}: failed; no retry'
            if label!='topology':
                r=load(out/label/'result.json'); t=r['persistent_transport']
                assert r['passed'] and r['drained'] and t['physical_drained'] and t['closed']
                assert r['packet_capture']['complete'] and not r['packet_capture']['omitted_events']
                assert all(w['ready_ns']==w['published_ns']==0 for w in t['work'])
        assert sha(backend)==identity['sha256']
        for name, digest in identity['libraries'].items(): assert sha(Path(name))==digest
    finally:
        save(out/'run-receipt.json', dict(driver_wall_seconds=time.monotonic()-started,
             allocated_bytes=allocated(out.parent), commands=commands, network_limit=4))
    print(json.dumps(dict(conditions=4, completed=True, allocated_bytes=allocated(out.parent))))


if __name__ == '__main__': main()
