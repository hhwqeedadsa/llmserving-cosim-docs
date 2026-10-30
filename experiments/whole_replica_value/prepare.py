"""Freeze prepared no-redundancy controls against existing full replicas."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def manifest(config,base):
    path=base/config['route_replay']['manifest']['path']
    assert sha(path)==config['route_replay']['manifest']['sha256']
    data=load(path);identity=deepcopy(data)
    for file in identity['files']:
        source=base/file['file']['path'];assert sha(source)==file['file']['sha256']
        file['file'].pop('path')
    return identity


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('prepared','eager-normal','eager-local','backend','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();prepared=args.prepared.resolve();repo=Path(__file__).resolve().parents[2]
    c=load(prepared/'service-whole.json');control=load(prepared/'service-primary.json')
    expected=deepcopy(c);expected.pop('whole_call_replication');expected['route_replay']['request_layout']='sparse_active_groups/1'
    assert expected==control
    identity=manifest(c,repo);assert len(identity['files'])==4
    refs={};reference_manifests=[]
    for name,path,protocol in [('normal',args.eager_normal,'rdma.json'),('local',args.eager_local,'rdma-whole-local.json')]:
        path=path.resolve();old=load(path/'input.json');reference_manifests.append(manifest(old,path))
        old['route_replay']['manifest']=c['route_replay']['manifest'];assert old==c
        assert load(path/'topology.json')==load(prepared/'topology/topology.json')
        assert load(path/'rdma.json')==load(prepared/protocol)
        assert load(path/'verification.json')['passed']
        refs[name]=dict(directory=str(path),sha256={n:sha(path/n) for n in ('input.json','result.json','frontend.json','topology.json','rdma.json','verification.json')})
    assert all(m==identity for m in reference_manifests)
    backend=args.backend.resolve();assert sha(backend)=='49cbc071754f1635596f789025e2a6e75d843e768730cef994b828129a1bb95a'
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    out=dict(passed=True,network_executed=False,platform_commit=commit,simulated_endpoints=128,ep_sizes=[64,64],
        logical_requests=4,control_execution_attempts=4,eager_execution_attempts=8,
        control_active_groups=[0],control_dormant_groups=[1],raw_source_identity=identity,
        references=refs,backend=dict(path=str(backend),sha256=sha(backend)),
        prepared=str(prepared),prepared_sha256={n:sha(prepared/n) for n in ('service-whole.json','service-primary.json','rdma.json','rdma-whole-local.json','topology/topology.json')},
        method_sha256=sha(Path(__file__).with_name('protocol.md')),
        scope='pre-execution matched logical input and deployment; no outcome optimization')
    with args.output.open('x') as stream:json.dump(out,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(dict(passed=True,network_executed=False,platform_commit=commit)))


if __name__=='__main__':main()
