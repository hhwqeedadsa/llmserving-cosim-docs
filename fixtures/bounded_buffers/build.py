"""Rebuild all five stream-layout consumers against existing pinned libraries."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from moe_ub_cosim.host_probe import probe

ROOT=Path(__file__).resolve().parents[2]


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-build',type=Path,required=True)
    p.add_argument('--stage',type=Path,required=True)
    a=p.parse_args();old=a.reference_build.resolve();out=a.stage.resolve();out.mkdir(exist_ok=False)
    (out/'objects').mkdir();(out/'bin').mkdir()
    previous=json.loads((old/'identity.json').read_text())
    records=json.loads((old/'records.json').read_text())
    modified={'backend/rdma/'+n for n in ('persistent_streams.cc','persistent_streams.hh',
        'remote_transactions.cc','remote_transactions.hh','remote_memory.hh','transaction_probe.cc',
        'transaction_session_validation.hh','transaction_session.cc')}
    for name,expected in previous['sources'].items():
        if name not in modified:assert sha(ROOT/name)==expected, 'unexpected source change: '+name
    protected={**previous['protected'],**previous['binaries']}
    control=Path('/dev/shm/moe-control-alarms-20261024/bin/ns3.42-moe-rdma-transaction-session')
    protected[str(control)]=sha(control)
    for path,expected in protected.items():assert sha(path)==expected
    sources={str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'backend/rdma').iterdir()
             if p.suffix in ('.cc','.hh')}
    sources['python/moe_ub_cosim/scaleout_transactions.py']=sha(ROOT/'python/moe_ub_cosim/scaleout_transactions.py')
    identity=dict(protected=protected,sources=sources,baseline_commit=subprocess.check_output(
        ['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip())
    (out/'identity.json').write_text(json.dumps(identity,indent=2)+'\n')
    current=[]
    def run(name,cmd):
        r=probe(cmd,out/name,rss_bytes=2**30,address_bytes=2**31,artifact_bytes=64*2**20,
                wall_seconds=60,artifact_roots=[out/'objects',out/'bin'],min_free_bytes=8*2**30)
        current.append(r);(out/'build-records.json').write_text(json.dumps(current,indent=2)+'\n')
        assert r['status']=='completed' and r['child_exit_code']==0 and not r['remaining_live_group_pids'],name
        assert sum(r['wall_seconds'] for r in current)<600
    for name in ('persistent_streams','remote_transactions','stream_probe','transaction_probe','transaction_session'):
        cmd=next(r['command'] for r in records if any(x.endswith('/'+name+'.cc') for x in r['command']))
        cmd=[part.replace(str(old/'objects'),str(out/'objects')) for part in cmd]
        run('compile-'+name,cmd)
    for name in ('stream-probe','transaction-probe','transaction-session'):
        cmd=next(r['command'] for r in records if any('link-'+name in path for path in r['artifact_roots']))
        cmd=[part.replace(str(old/'objects'),str(out/'objects')).replace(str(old/'bin'),str(out/'bin')) for part in cmd]
        run('link-'+name,cmd)
    assert all(sha(path)==expected for path,expected in protected.items())
    identity['binaries']={str(p):sha(p) for p in (out/'bin').iterdir()}
    (out/'identity.json').write_text(json.dumps(identity,indent=2)+'\n')
    print(json.dumps(identity['binaries']))


if __name__=='__main__':main()
