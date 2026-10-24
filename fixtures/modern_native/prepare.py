"""Freeze an existing fixed session runtime and four already supplied routes."""
import argparse
from copy import deepcopy
from pathlib import Path
import shutil
from fixtures.modern_native.stage import load,save,sha
from moe_ub_cosim.modern_native_layout import ModernNativeLayout


def main():
    p=argparse.ArgumentParser()
    for key in ('repository','stage'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();out=s/'runtime';out.mkdir()
    prior=Path('/dev/shm/moe-self-peer-service-20261024');identity=load(prior/'build.json')
    for name in ('transaction-session','run-transaction-session'):
        src=prior/'runtime'/name;assert sha(src)==identity['built_runtime'][name];shutil.copyfile(src,out/name);(out/name).chmod(0o755)
    (out/'lib').mkdir()
    for src,h in identity['libraries'].items():
        src=prior/'runtime/lib'/Path(src).name;assert sha(src)==h;shutil.copyfile(src,out/'lib'/src.name)
    save(s/'runtime-identity.json',dict(actual_native_commit='68a07f7d5e53bbe97d275af41d4964ad01b864f3',
        runtime={str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file()},
        original_build=identity,clean_native_build=False))
    inputs=s/'inputs';inputs.mkdir();base=load(repo/'examples/modern_deepep_native/input.json');save(inputs/'synthetic.json',base)
    provenance=load(repo/'docs/review_artifacts/modern_operations_20261022/provenance.json');originals=[]
    for row in provenance['files']:
        src=repo/row['path'];assert sha(src)==row['sha256'];originals.append(load(src))
    # The original provenance fixes entry1/layer3; each rank contributes one token.
    selected=[row for row in provenance['selections'] if row['call']==0]
    tokens=[raw[1]['3'] for raw in originals]
    assert [r['choices'] for r in selected]==[t[0] for t in tokens] and len(tokens)==4
    real=deepcopy(base);real['modern']['model'].update(experts=256,top_k=8,hidden_size=7168)
    real['modern']['execution'].update(ranks=4,max_tokens_per_rank=1,num_sms=2,dispatch_warps=2,combine_warps=2,
        call_contexts=5,requested_contexts=5,local_devices=[[0,1] for _ in range(4)])
    real['modern']['calls']=[dict(name='entry1.layer3',tokens=tokens)]
    real['modern']['limits'].update(max_events=65536,max_operations=16384,max_output_bytes=16*2**20)
    real['deployment']=[dict(rank=r,node=n,devices=deepcopy(base['deployment'][0]['devices'])) for r,n in enumerate((0,1,4,5))]
    save(inputs/'four-routes.json',real)
    save(s/'route-provenance.json',dict(source=provenance['source'],original_files=provenance['files'],selections=selected,
        entry=1,layer=3,original_bytes_retained_in_repository=True,
        scope='four actual routing rows only; explicit EP4/H7168/BF16 functional branch, not a full DeepSeek-R1 deployment'))
    topology=load(s/'topology/topology.json');protocol=load(repo/'examples/modern_deepep_native/rdma.json');checks={}
    for label,cfg in (('synthetic',base),('four-routes',real)):
        layout=ModernNativeLayout(topology,protocol,cfg)
        checks[label]=dict(events=len(layout.plan['events']),operations=len(layout.plan['operations']),
            paths={path:sum(o['path']==path for o in layout.plan['operations']) for path in ('local','gin_self','rdma')},
            prospective_connections=len(layout.connections),initialization_bytes=sum(len(x['hex'])//2 for w in layout.initial['windows'] for x in w['segments']))
    save(s/'preflight.json',dict(passed=True,cases=checks,network_executed=False,
        limits_unchanged=True,source_routes_checked=True,source_native_session_sha256=sha(out/'transaction-session')))


if __name__=='__main__':main()
