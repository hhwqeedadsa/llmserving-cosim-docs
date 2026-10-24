"""Repair a code-reviewed legacy schema admission gap; no network reexecution."""
import argparse
from copy import deepcopy
from pathlib import Path
import subprocess
from fixtures.self_peer_service.stage import load,save,sha,run


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--check-only',action='store_true')
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve()
    assert load(s/'recovery.json')['passed'] and not (s/'admission-guard.json').exists()
    if not a.check_only:
        original=load(s/'build.json');save(s/'build-before-admission-guard.json',original)
        frozen={str(p.relative_to(s)):sha(p) for p in (s/'objects/remote_transactions.o',s/'objects/transaction_session.o',s/'runtime/transaction-session')}
        save(s/'admission-guard-frozen.json',frozen)
        commands=load(s/'commands.json')
        compile_cmd=next(r['command'] for r in commands if r['label']=='compile-transaction_probe-validation-fix')
        link_cmd=next(r['command'] for r in commands if r['label']=='link-transaction-probe-current-baseline')
        # No successful network is rerun; only the changed main consumer is rebuilt.
        run(repo,s,'compile-probe-admission-guard',compile_cmd)
        run(repo,s,'link-probe-admission-guard',link_cmd)
        run(repo,s,'legacy-schema-rejects',['python3','-B','-m','fixtures.self_peer_service.admission_guard',
            '--repository',repo,'--stage',s,'--check-only'])
        return
    original=load(s/'build-before-admission-guard.json');frozen=load(s/'admission-guard-frozen.json')
    cfg=load(s/'inputs/mixed.json');rejected=[];logs=s/'admission-guard';logs.mkdir()
    for schema in (1,2,3,4):
        for declared in (False,True):
            c=deepcopy(cfg);c['schema_version']='moe-rdma-transactions/'+str(schema)
            if not declared:c.pop('self_peer_model')
            for w in c['windows']:
                w['hex']='00'*w.pop('bytes');w.pop('initialization');w.pop('segments')
            if schema<4:c.pop('local_memory_model')
            if schema<3:c.pop('source_model')
            if schema<2:
                c.pop('local_model');c['operations']=[o for o in c['operations'] if o['kind']=='remote']
                for op in c['operations']:op.pop('kind')
            name=f'v{schema}-'+('model' if declared else 'missing-model');path=logs/(name+'.json');output=logs/(name+'-unexpected.json');save(path,c)
            cmd=list(map(str,[s/'runtime/run-transaction-probe',s/'topology/topology.json',s/'inputs/protocol.json',path,output]))
            with (logs/(name+'.stdout')).open('w') as out,(logs/(name+'.stderr')).open('w') as err:
                r=subprocess.run(cmd,cwd=s,stdout=out,stderr=err,timeout=3)
            assert r.returncode==1 and not output.exists()
            stderr=(logs/(name+'.stderr')).read_text()
            assert ('self-peer requires v5' if declared else 'self-peer model is missing') in stderr,stderr
            rejected.append(dict(case=name,command=cmd,cwd=str(s),exit_code=r.returncode))
    assert all(sha(s/p)==h for p,h in frozen.items())
    assert all(sha(Path(p))==h==sha(s/'runtime/lib'/Path(p).name) for p,h in original['libraries'].items())
    current=deepcopy(original)
    current['sources']['backend/rdma/transaction_probe.cc']=sha(repo/'backend/rdma/transaction_probe.cc')
    current['built_runtime']={str(p.relative_to(s/'runtime')):sha(p) for p in (s/'runtime').rglob('*') if p.is_file()}
    save(s/'build.json',current)
    save(s/'admission-guard.json',dict(passed=True,legacy_schema_rejects=rejected,unchanged_runtime_objects=frozen,
        additional_network_executions=0,original_network_binary_in='portable-initial.tar.gz',
        final_probe_network_reexecuted=False,change='pure pre-Fabric admission for self declarations in schemas1-4; v5 executor, core/session/8libraries unchanged'))


if __name__=='__main__':main()
