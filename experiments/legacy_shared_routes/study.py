"""Prepare and execute two complete real-selection groups via public commands."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.route_evidence import iter_sources,validate_manifest

if not __debug__:raise RuntimeError('Study requires assertions; do not run with -O')
ROOT=Path(__file__).resolve().parents[2]
load=lambda path:json.loads(Path(path).read_bytes())
sha=lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def cli(out,name,args):
    command=[sys.executable,'-B','-m','moe_ub_cosim','scaleout',*map(str,args)]
    r=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=175)
    save(out/(name+'-command.json'),dict(command=command,cwd=str(ROOT),exit_code=r.returncode))
    (out/(name+'-stdout.log')).write_text(r.stdout);(out/(name+'-stderr.log')).write_text(r.stderr)
    if r.returncode:raise RuntimeError(name+' failed; logs retained in '+str(out))


def selections(path):
    manifest=load(path);validate_manifest(manifest)
    assert manifest['source']['kind']=='user_provided_model_routes'
    assert manifest['source']['dataset_id']=='core12345/MoE_expert_selection_trace'
    assert manifest['source']['collector_model_id']=='cognitivecomputations/DeepSeek-R1-AWQ'
    assert manifest['model']['hidden_dim']==7168 and len(manifest['files'])==15
    assert manifest['layers'][3]['num_experts']==256 and manifest['layers'][3]['top_k']==8
    sizes=[(ROOT/f['file']['path']).stat().st_size for f in manifest['files']]
    assert max(sizes)<8*2**20 and sum(sizes)<64*2**20
    assert len({f['file']['sha256'] for f in manifest['files']})==15
    tokens=[[[] for _ in range(32)] for _ in range(2)];records=[]
    for i,(file_id,source) in enumerate(iter_sources(manifest)):
        group=int(i>=8);rank=i if group==0 else i-8;choices=source['raw'][1]['3'][0]
        tokens[group][rank].append(choices)
        records.append(dict(file_id=file_id,group=group,source_rank=rank,file=source['file'],
            original_path=source['original_path'],layer=3,entry=1,token_row=0,input_token_index=source['prompt_tokens']))
    assert [sum(map(len,t)) for t in tokens]==[8,7]
    return manifest,tokens,records


def prepare(a):
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    manifest,tokens,records=selections(a.source_manifest)
    build=load(a.build);backend=Path(build['legacy_deepep'])
    assert build['passed'] and sha(backend)==build['backend_sha256']
    for name,expected in build['sources'].items():assert sha(ROOT/name)==expected,name
    libraries={str(backend.parent/'runtime/lib'/name):expected for name,expected in build['library_hashes'].items()}
    for path,expected in libraries.items():assert sha(path)==expected,path
    config=load(ROOT/'examples/scaleout_clos128/clos-128.json')
    config['deployment']['gpu_type']='NONE';config['intra_host']['enabled']=False
    save(out/'topology-config.json',config)
    cli(out,'topology',['topology','--config',out/'topology-config.json','--output',out/'topology'])
    topology=load(out/'topology/topology.json')
    nodes={n['id']:n for n in topology['nodes']}
    access={link['src']:link['dst'] for link in topology['links'] if link['tier']=='access'}
    assert len(access)==128 and len(nodes)==148
    mappings=[list(range(0,128,4))]
    other=[g for g in sorted(access) if g not in mappings[0] and access[g]!=128]
    mappings.append([g for pod in (0,1) for g in [g for g in other if nodes[g]['pod']==pod][:16]])
    assert len(mappings[1])==32 and not set(mappings[0])&set(mappings[1])
    assert all(access[g]!=128 for g in mappings[1])
    groups=[]
    for i in range(2):
        folder=out/f'group-{i}';folder.mkdir()
        p=load(ROOT/'examples/deepep_indexed/input.json')
        p['calls']=[dict(name=f'R1-layer3-entry1-group{i}',tokens=tokens[i])]
        save(folder/'input.json',p)
        save(folder/'binding.json',dict(schema_version='moe-legacy-native-binding/1',rank_to_gpu=mappings[i],
            queue_depth=32,publication='legacy_sync_batch4/1',memory_model='single_span_zero_cost_visibility_expert_copy/1'))
        cli(out,f'plan-{i}',['deepep-plan','--format','indexed-jsonl','--config',folder/'input.json','--output',folder/'plan'])
        groups.append(dict(id=f'group-{i}',plan=f'group-{i}/plan',binding=f'group-{i}/binding.json'))
    save(out/'shared.json',dict(schema_version='moe-legacy-shared-input/1',groups=groups))
    protocol=load(ROOT/'examples/scaleout_clos128/rdma.json')
    protocol.update(payload_bytes=1024,chunk_bytes=4096,switch_counter_storage='port_dense',queue_metrics=True,
        packet_capture=dict(nodes=sorted(mappings[0]+mappings[1]+[128,132,133]),start_ns=0,
                            end_ns=5000000,max_events=1000000,wire_headers=True))
    protocol['limits'].update(max_messages=65536,max_queue_events=2000000,queue_capture='summary',max_sim_ns=5000000,
        wall_seconds=180,rss_bytes=3*2**30,address_bytes=6*2**30,artifact_bytes=768*2**20,result_json_bytes=512*2**20)
    save(out/'normal.json',protocol)
    for case,mode in [('local','live_ecmp/1'),('prefix','explicit_prefix/1')]:
        p=deepcopy(protocol);p['link_failures']=dict(mode='switch_links/2',events=[dict(src=128,dst=132,time_ns=4000,
            route_update_delay_ns=60000,restore_time_ns=24000,restore_route_update_delay_ns=60000)])
        p['local_failover']=dict(mode=mode)
        if case=='prefix':p['local_failover']['max_events']=100000
        save(out/(case+'.json'),p)
    source=load(ROOT/'examples/scaleout_rdma_transactions/selective-chain.json')
    transport={key:source[key] for key in ('transport_recovery','dcqcn_reference')}
    transport['transport_recovery'].update(initial_rto_ns=17000,max_events=500000,inflight_bytes=4096,receive_buffer_bytes=8192)
    transport['dcqcn_reference']['max_events']=200000;save(out/'transport.json',transport)
    save(out/'selection.json',dict(source=manifest['source'],records=records,requests=15,selected_choices=120,
        per_group_requests=[8,7],network_endpoints=128,ep_sizes=[32,32],rank_to_gpu=mappings,
        endpoint_to_tor=access,remote_revision_verified=False,activation_dtype_assumed='bf16',
        physical_gpu_executed=False,full_inference_executed=False))
    cli(out,'validate',['deepep-shared-run','--config',out/'shared.json','--topology',out/'topology',
        '--protocol',out/'normal.json','--transport',out/'transport.json','--backend',backend,
        '--output',out/'validation','--validate-only'])
    frozen=dict(platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        backend=str(backend),backend_sha256=sha(backend),compiled_sources=build['sources'],libraries=libraries,
        source_manifest=str(a.source_manifest.resolve()),source_manifest_sha256=sha(a.source_manifest),
        input_sha256={str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file()},
        code_sha256={str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__),Path(__file__).with_name('README.md'),
                     *[ROOT/'python/moe_ub_cosim'/name for name in ['legacy_shared.py','legacy_native.py','legacy_audit.py','legacy_shared_audit.py']]]},
        cases=['normal','local','prefix'],native_network_executed=False)
    save(out/'frozen.json',frozen)
    print(json.dumps(dict(prepared=True,validation=load(out/'validation/validation.json'),native_network_executed=False)))


def verify(study):
    f=load(study/'frozen.json')
    assert sha(f['backend'])==f['backend_sha256']
    for path,expected in f['input_sha256'].items():assert sha(study/path)==expected,path
    for path,expected in {**f['compiled_sources'],**f['code_sha256']}.items():assert sha(ROOT/path)==expected,path
    for path,expected in f['libraries'].items():assert sha(path)==expected,path
    assert sha(f['source_manifest'])==f['source_manifest_sha256']
    return f


def execute(a):
    study=a.study.resolve();f=verify(study);root=study.parent
    assert a.case in f['cases'];run=root/('run-'+a.case)
    if a.action=='run':
        assert not run.exists()
        cli(root,'run-'+a.case,['deepep-shared-run','--config',study/'shared.json','--topology',study/'topology',
            '--protocol',study/(a.case+'.json'),'--transport',study/'transport.json','--backend',f['backend'],'--output',run])
    else:
        cli(root,'audit-'+a.case,['deepep-shared-audit','--run',run,'--protocol',study/(a.case+'.json'),
            '--output',root/(a.case+'-audit.json')])


def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='action',required=True)
    prep=s.add_parser('prepare');prep.add_argument('--source-manifest',type=Path,required=True)
    prep.add_argument('--build',type=Path,required=True);prep.add_argument('--output',type=Path,required=True)
    for name in ('run','audit'):
        c=s.add_parser(name);c.add_argument('--study',type=Path,required=True)
        c.add_argument('--case',choices=('normal','local','prefix'),required=True)
    a=p.parse_args();prepare(a) if a.action=='prepare' else execute(a)


if __name__=='__main__':main()
