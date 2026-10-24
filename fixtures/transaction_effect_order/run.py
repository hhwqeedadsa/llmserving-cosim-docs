"""Public CLI reproduction of delayed old writes; no research performance claim."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
def save(p, value): p.write_text(json.dumps(value, indent=2) + '\n')
def read(p): return json.loads(p.read_text())
def sha(p):
    with p.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()

def work(policy):
    isolated = policy == 'isolated'
    data = 4128 if isolated else 4096
    connection = lambda i: dict(id=i, src=0, dst=4, plane=0, request_port=25000+2*i,
        response_port=25001+2*i, source_nic=1, destination_nic=1, queue_depth=4)
    remote = lambda i, c, **kw: dict(id=i, kind='remote', connection=c,
        key=7, aggregate_requests=False, **kw)
    return dict(schema_version='moe-rdma-transactions/5',
        connections=[connection(0), connection(1)],
        windows=[dict(owner=4, key=7, address=4096, allow_atomic=True, bytes=64,
                      initialization='zero_fill/1', segments=[])],
        operations=[
            remote(0, 0, verb='put', address=4096, hex='11'*8, after=[]),
            dict(id=1, kind='local', rank=0, duration_ns=20000,
                 after=[dict(operation=0, event='request_acked' if policy=='wait' else 'prepared')]),
            remote(2, 1, verb='put', address=data, hex='22'*8,
                   after=[dict(operation=1, event='completed')]),
            remote(3, 1, verb='fetch_add', address=4104, value=2,
                   after=[dict(operation=2, event='request_acked')]),
            dict(id=4, kind='local', rank=4, duration_ns=100000, after=[]),
            dict(id=5, kind='local', rank=4, duration_ns=0,
                 after=[dict(operation=4, event='completed')],
                 local_wait=[dict(key=7, address=4104, compare='eq', value=2)],
                 copy=dict(source=dict(key=7, address=data, bytes=8),
                           target=dict(key=7, address=4112)))],
        local_model=dict(mode='serial_per_gpu/1', duration_source='declared_ns'),
        source_model=dict(capture='registered_window_at_publish/1', release='transaction_completed/1'),
        local_memory_model=dict(mode='guarded_copy/1', acquire='admission/1',
                                capture='start/1', commit='completion/1'),
        submission_model=dict(mode='explicit_prefix/1'),
        transport_recovery=dict(mode='go_back_n/1', initial_rto_ns=80000,
                                max_rto_ns=640000, max_retries=3, max_events=1000))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--phase', choices=['before','after'], required=True)
    a=p.parse_args(); out=a.output.resolve(); out.mkdir(exist_ok=False)
    backend=a.backend.resolve(); identity=sha(backend); commands=[]; start=time.monotonic()
    env=dict(os.environ, PYTHONPATH=str(ROOT/'python'), PYTHONDONTWRITEBYTECODE='1')
    def cli(label, args, expected=0):
        assert time.monotonic()-start < 180 and shutil.disk_usage(out).free >= 8*2**30
        assert sum(f.stat().st_size for f in out.rglob('*') if f.is_file()) < 64*2**20
        cmd=[sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', *map(str,args)]
        with (out/(label+'.stdout')).open('w') as so, (out/(label+'.stderr')).open('w') as se:
            r=subprocess.run(cmd, cwd=ROOT, env=env, stdout=so, stderr=se, timeout=60)
        commands.append(dict(label=label, command=cmd, exit_code=r.returncode, cwd=str(ROOT)))
        save(out/'commands.json', commands)
        assert r.returncode==expected, label
    cli('topology',['topology','--config',ROOT/'examples/scaleout_rdma/small-clos-100g.json',
                    '--output',out/'topology'])
    protocol=read(ROOT/'examples/scaleout_rdma_streams/rdma-loss.json')
    protocol['faults']=[dict(node=4,port=0,message_id=0,kind='data',sequence=0,
                            count=1,start_ns=0,end_ns=5000000)]
    protocol['packet_capture']['nodes']=[0,4]
    save(out/'protocol.json', protocol)
    for name in ['unsafe','isolated','wait']:
        save(out/(name+'.json'),work(name))
        expected=1 if name=='unsafe' and a.phase=='after' else 0
        cli(name,['rdma-transaction-probe','--topology',out/'topology',
            '--protocol',out/'protocol.json','--config',out/(name+'.json'),
            '--backend',backend,'--output',out/name],expected)
    assert sha(backend)==identity
    save(out/'run.json',dict(phase=a.phase,backend=str(backend),backend_sha256=identity,
        platform_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        seconds=time.monotonic()-start,passed=True))
    print(json.dumps(dict(passed=True,phase=a.phase,commands=len(commands))))
if __name__=='__main__': main()
