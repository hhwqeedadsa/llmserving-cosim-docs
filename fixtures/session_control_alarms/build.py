"""Bounded session-only incremental compile using a verified fixed build recipe."""
import argparse
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.host_probe import probe


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-build',type=Path,required=True)
    p.add_argument('--stage',type=Path,required=True)
    args=p.parse_args();stage=args.stage.resolve();stage.mkdir(exist_ok=False)
    repo=Path(__file__).resolve().parents[2];old=args.reference_build.resolve()
    identity=json.loads((old/'identity.json').read_text())
    for name,expected in identity['sources'].items():
        if name not in ('backend/rdma/transaction_session.cc','python/moe_ub_cosim/transaction_session.py'):
            assert digest(repo/name)==expected, 'unaccounted source change: '+name
    for name,expected in {**identity['protected'],**identity['binaries']}.items():
        assert digest(name)==expected, 'reference identity changed: '+name
    records=json.loads((old/'records.json').read_text())
    compile_command=next(r['command'] for r in records if any(a.endswith('/transaction_session.cc') for a in r['command']))
    link_command=next(r['command'] for r in records if any('link-transaction-session' in p for p in r['artifact_roots']))
    objects=stage/'objects';objects.mkdir();binaries=stage/'bin';binaries.mkdir()
    old_object=str(old/'objects/transaction_session.cc.o');new_object=str(objects/'transaction_session.cc.o')
    def rewrite(command):
        return [part.replace(old_object,new_object).replace(
                str(old/'bin/ns3.42-moe-rdma-transaction-session'),
                str(binaries/'ns3.42-moe-rdma-transaction-session')) for part in command]
    protected={name:digest(name) for name in [*identity['protected'],*identity['binaries'],
                str(old/'objects/remote_transactions.cc.o'),str(old/'objects/persistent_streams.cc.o'),
                '/dev/shm/moe-persistent-recovery-20261024/objects/libmoe-rdma-fabric.a']}
    (stage/'input-identity.json').write_text(json.dumps(dict(protected=protected,
        modified_source=digest(repo/'backend/rdma/transaction_session.cc'),
        commands=[rewrite(compile_command),rewrite(link_command)]),indent=2)+'\n')
    reports=[]
    for name,command in [('compile',rewrite(compile_command)),('link',rewrite(link_command))]:
        result=probe(command,stage/('build-'+name),rss_bytes=2**30,address_bytes=2**31,
                     artifact_bytes=64*2**20,wall_seconds=60,artifact_roots=[objects,binaries],min_free_bytes=8*2**30)
        reports.append(result)
        (stage/'build-records.json').write_text(json.dumps(reports,indent=2)+'\n')
        assert result['status']=='completed' and not result['remaining_live_group_pids'], 'stop failed build, preserve logs'
    for path,expected in protected.items():assert digest(path)==expected
    (stage/'backend.json').write_text(json.dumps(dict(path=str(binaries/'ns3.42-moe-rdma-transaction-session'),
        sha256=digest(binaries/'ns3.42-moe-rdma-transaction-session')),indent=2)+'\n')
    print((stage/'backend.json').read_text())


if __name__=='__main__':main()
