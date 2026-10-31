"""Freeze a public-normalizer input and perform a bounded two-call admission."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

if not __debug__:raise RuntimeError('Do not run with -O')
ROOT=Path(__file__).resolve().parents[2]
load=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
save=lambda p,x:p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')


def invoke(out,name,command,expect_success=True):
    r=subprocess.run(list(map(str,command)),cwd=ROOT,capture_output=True,text=True,timeout=115)
    save(out/(name+'-command.json'),dict(command=list(map(str,command)),cwd=str(ROOT),exit_code=r.returncode,
        success_required=expect_success))
    (out/(name+'-stdout.log')).write_text(r.stdout);(out/(name+'-stderr.log')).write_text(r.stderr)
    if expect_success and r.returncode:raise RuntimeError(name+' failed; logs retained')
    return r


def cli(out,name,args,expect_success=True):
    return invoke(out,name,[sys.executable,'-B','-m','moe_ub_cosim','scaleout',*args],expect_success)


def prepare(a):
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    manifest=load(a.manifest)
    assert len(manifest['files'])==15
    sizes=[(ROOT/r['file']['path']).stat().st_size for r in manifest['files']]
    assert max(sizes)<=8*2**20 and sum(sizes)<=64*2**20
    assert len({r['file']['sha256'] for r in manifest['files']})==15
    save(out/'source-manifest.json',manifest)
    invoke(out,'normalize',[sys.executable,'-B','-m','moe_ub_cosim.route_evidence',
        '--manifest',out/'source-manifest.json','--output-dir',out/'normalized'])
    normalized=load(out/'normalized/report.json')
    assert normalized['raw_schema_verified'] and normalized['source']['kind']=='user_provided_model_routes'
    assert normalized['model']['hidden_dim']==7168
    assert all(s['available_decode_records']>=64 for s in normalized['files'])
    assert len(normalized['files'])==15 and normalized['records']<=150000
    assert sha(out/'normalized/normalized_decode.jsonl')==normalized['normalized_sha256']
    topology=load(ROOT/'examples/scaleout_clos128/clos-128.json')
    topology['deployment']['gpu_type']='NONE';topology['intra_host']['enabled']=False
    save(out/'topology-config.json',topology)
    cli(out,'topology',['topology','--config',out/'topology-config.json','--output',out/'topology'])
    t=load(out/'topology/topology.json');nodes={n['id']:n for n in t['nodes']}
    access={r['src']:r['dst'] for r in t['links'] if r['tier']=='access'};assert len(access)==128
    mappings=[list(range(0,128,4))]
    other=[g for g in sorted(access) if g not in mappings[0] and access[g]!=128]
    mappings.append([g for pod in (0,1) for g in [g for g in other if nodes[g]['pod']==pod][:16]])
    assert len(mappings[1])==32 and not set(mappings[0])&set(mappings[1])
    backend=load(a.backend_identity);assert backend['passed']
    assert sha(backend['legacy_deepep'])==backend['backend_sha256']
    for path,digest in backend['sources'].items():assert sha(ROOT/path)==digest
    save(out/'backend-identity.json',backend)
    policy=dict(layers=[3,31,60],first_decode_entry=1,decode_steps=64,windows=[1,4,16,64],shuffle_seeds=[11,29,47],
        per_group_requests=[8,7],ep_sizes=[32,32],experts=256,top_k=8,hidden_size=7168,activation_bytes=14336,
        rank_to_gpu=mappings,access_rate_bps=400000000000,network_endpoints=128,
        source_order=[r['file_id'] for r in manifest['files']],network_executed=False,
        source_steps_are_autoregressive=True,simultaneous_step_alignment_is_assumed=True)
    save(out/'policy.json',policy)
    frozen=dict(platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        input_sha256={str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file()},
        source_manifest_sha256=sha(a.manifest),method_sha256=sha(Path(__file__).with_name('README.md')),
        preparation_sha256=sha(Path(__file__)),backend_sha256=backend['backend_sha256'])
    save(out/'frozen.json',frozen)
    print(json.dumps(dict(prepared=True,decode_records=normalized['records'],new_network_runs=0)))


def admission(a):
    study=a.study.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    frozen=load(study/'frozen.json')
    for name,digest in frozen['input_sha256'].items():assert sha(study/name)==digest
    assert sha(Path(__file__))==frozen['preparation_sha256']
    policy=load(study/'policy.json');data={}
    with (study/'normalized/normalized_decode.jsonl').open() as stream:
        for line in stream:
            row=json.loads(line)
            if row['layer_id']==3 and row['source_entry'] in (1,2):data[row['file_id'],row['source_entry']]=row['expert_ids']
    groups=[];models=[]
    for group,(first,last) in enumerate(((0,8),(8,15))):
        folder=out/f'group-{group}';folder.mkdir()
        config=load(ROOT/'examples/deepep_indexed/input.json')
        config['calls']=[]
        for entry in (1,2):
            tokens=[[] for _ in range(32)]
            for rank,i in enumerate(range(first,last)):tokens[rank]=[data[policy['source_order'][i],entry]]
            config['calls'].append(dict(name=f'layer3-entry{entry}-group{group}',tokens=tokens))
        config['limits'].update(max_logical_operations=35000,max_logical_events=140000)
        save(folder/'input.json',config);models.append(config)
        save(folder/'binding.json',dict(schema_version='moe-legacy-native-binding/1',rank_to_gpu=policy['rank_to_gpu'][group],
            queue_depth=32,publication='legacy_sync_batch4/1',memory_model='single_span_zero_cost_visibility_expert_copy/1'))
        cli(out,f'plan-{group}',['deepep-plan','--format','indexed-jsonl','--config',folder/'input.json','--output',folder/'plan'])
        groups.append(dict(id=f'group-{group}',plan=f'group-{group}/plan',binding=f'group-{group}/binding.json'))
    save(out/'shared.json',dict(schema_version='moe-legacy-shared-input/1',groups=groups))
    protocol=load(ROOT/'examples/scaleout_clos128/rdma.json')
    protocol.update(switch_counter_storage='port_dense',queue_metrics=False)
    protocol['limits'].update(max_messages=65536,max_queue_events=2000000,queue_capture='summary',
        wall_seconds=120,rss_bytes=2*2**30,address_bytes=4*2**30,result_json_bytes=256*2**20)
    save(out/'normal.json',protocol)
    source=load(ROOT/'examples/scaleout_rdma_transactions/selective-chain.json')
    transport={k:source[k] for k in ('transport_recovery','dcqcn_reference')}
    transport['dcqcn_reference']['max_events']=200000
    transport['transport_recovery'].update(initial_rto_ns=17000,max_events=500000,inflight_bytes=4096,receive_buffer_bytes=8192)
    save(out/'transport.json',transport)
    backend=load(study/'backend-identity.json')['legacy_deepep']
    result=cli(out,'admission',['deepep-shared-run','--config',out/'shared.json','--topology',study/'topology',
        '--protocol',out/'normal.json','--transport',out/'transport.json','--backend',backend,'--output',out/'validation',
        '--validate-only'],expect_success=False)
    accepted=result.returncode==0
    if not accepted:assert 'complete plan exceeds native lifetime budget' in result.stderr
    assert not (out/'validation/result.json').exists() and not (out/'validation/execution-monitor').exists()
    save(out/'admission-summary.json',dict(passed=True,public_precheck_accepted=accepted,exit_code=result.returncode,
        rejection='complete plan exceeds native lifetime budget' if not accepted else None,
        per_group_operations=[sum(2*32*256+2*sum(len(c) for r in call['tokens'] for c in r) for call in x['calls']) for x in models],
        native_admission_executed=False,native_network_executed=False,full_zero_token_notifications_preserved=True))
    print((out/'admission-summary.json').read_text())


def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='action',required=True)
    x=s.add_parser('prepare');x.add_argument('--manifest',type=Path,required=True)
    x.add_argument('--backend-identity',type=Path,required=True);x.add_argument('--output',type=Path,required=True)
    x=s.add_parser('admission');x.add_argument('--study',type=Path,required=True);x.add_argument('--output',type=Path,required=True)
    a=p.parse_args();prepare(a) if a.action=='prepare' else admission(a)


if __name__=='__main__':main()
