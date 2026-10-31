"""Prospective legacy communication pilot with locally hash-checked R1 choices."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.route_evidence import iter_sources, validate_manifest

if not __debug__:
    raise RuntimeError("Study requires Python assertions; do not run with -O")

ROOT=Path(__file__).resolve().parents[2]
load=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def cli(out,name,args):
    command=[sys.executable,'-B','-m','moe_ub_cosim','scaleout',*map(str,args)]
    result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=115)
    save(out/(name+'-command.json'),dict(command=command,cwd=str(ROOT),exit_code=result.returncode))
    (out/(name+'-stdout.log')).write_text(result.stdout);(out/(name+'-stderr.log')).write_text(result.stderr)
    if result.returncode:raise RuntimeError(name+' failed; retained logs: '+str(out))


def read_selection(path):
    manifest=load(path);validate_manifest(manifest)
    assert manifest['source']['kind']=='user_provided_model_routes'
    assert manifest['source']['dataset_id']=='core12345/MoE_expert_selection_trace'
    assert manifest['source']['collector_model_id']=='cognitivecomputations/DeepSeek-R1-AWQ'
    assert manifest['model']['hidden_dim']==7168 and len(manifest['files'])==15
    layer=manifest['layers'][3];assert layer['kind']=='moe' and layer['num_experts']==256 and layer['top_k']==8
    sizes=[(ROOT/row['file']['path']).stat().st_size for row in manifest['files']]
    assert max(sizes)<=8*2**20 and sum(sizes)<=64*2**20
    assert len({row['file']['sha256'] for row in manifest['files']})==15
    tokens=[[] for _ in range(32)];records=[];counts=Counter()
    for rank,(file_id,source) in enumerate(iter_sources(manifest)):
        choices=source['raw'][1]['3'][0]
        tokens[rank].append(choices);counts.update(choices)
        records.append(dict(file_id=file_id,source_rank=rank,original_path=source['original_path'],
            file=source['file'],layer=3,entry=1,token_row=0,input_token_index=source['prompt_tokens']))
    assert len(records)==15 and sum(counts.values())==120
    return manifest,tokens,records,counts


def prepare(a):
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    manifest,tokens,records,counts=read_selection(a.source_manifest)
    build=load(a.build);backend=Path(build['legacy_deepep'])
    provenance=load(a.build.parent/'provenance.json')
    for name,expected in provenance['sources'].items():assert sha(ROOT/name)==expected
    for name in build['linked_ns3_libraries']:assert sha(name)==build['library_hashes'][Path(name).name]
    topology=load(ROOT/'examples/scaleout_clos128/clos-128.json')
    topology['deployment']['gpu_type']='NONE';topology['intra_host']['enabled']=False
    save(out/'topology-config.json',topology)
    cli(out,'topology',['topology','--config',out/'topology-config.json','--output',out/'topology'])
    input_config=load(ROOT/'examples/deepep_indexed/input.json')
    input_config['calls']=[dict(name='R1-layer3-entry1-15-local-files',tokens=tokens)]
    save(out/'plan-input.json',input_config)
    cli(out,'plan',['deepep-plan','--format','indexed-jsonl','--config',out/'plan-input.json','--output',out/'plan'])
    binding=dict(schema_version='moe-legacy-native-binding/1',rank_to_gpu=list(range(0,128,4)),queue_depth=32,
        publication='legacy_sync_batch4/1',memory_model='single_span_zero_cost_visibility_expert_copy/1')
    save(out/'binding.json',binding)
    protocol=load(ROOT/'examples/scaleout_clos128/rdma.json')
    protocol.update(payload_bytes=1024,chunk_bytes=4096,switch_counter_storage='port_dense',queue_metrics=False,
        packet_capture=dict(nodes=binding['rank_to_gpu'],start_ns=0,end_ns=5000000,max_events=1000000,wire_headers=True))
    protocol['limits'].update(max_messages=65536,max_queue_events=2000000,queue_capture='summary',max_sim_ns=5000000,
        wall_seconds=120,rss_bytes=2*2**30,address_bytes=4*2**30,artifact_bytes=512*2**20,result_json_bytes=256*2**20)
    save(out/'normal.json',protocol)
    for case,mode in [('local','live_ecmp/1'),('prefix','explicit_prefix/1')]:
        p=deepcopy(protocol);p['link_failures']=dict(mode='switch_links/2',events=[dict(src=128,dst=132,
            time_ns=4000,route_update_delay_ns=60000,restore_time_ns=24000,restore_route_update_delay_ns=60000)])
        p['local_failover']=dict(mode=mode)
        if mode=='explicit_prefix/1':p['local_failover']['max_events']=100000
        save(out/(case+'.json'),p)
    source=load(ROOT/'examples/scaleout_rdma_transactions/selective-chain.json')
    transport={key:source[key] for key in ('transport_recovery','dcqcn_reference')}
    transport['transport_recovery'].update(initial_rto_ns=17000,max_events=500000,inflight_bytes=4096,receive_buffer_bytes=8192)
    transport['dcqcn_reference']['max_events']=100000;save(out/'transport.json',transport)
    save(out/'selection.json',dict(source=manifest['source'],model=manifest['model'],records=records,
        requests=15,data_active_ranks=15,total_endpoints=128,ep_size=32,expert_counts=dict(sorted(counts.items())),
        routing_is_real_local_selection=True,remote_revision_verified=False,activation_dtype_assumed='bf16',
        physical_gpu_executed=False,collector_precision=manifest['source']['precision']))
    files={str(path.relative_to(out)):sha(path) for path in out.rglob('*') if path.is_file()}
    frozen=dict(platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        backend=str(backend),backend_sha256=sha(backend),compiled_sources=provenance['sources'],library_hashes=build['library_hashes'],
        linked_libraries=build['linked_ns3_libraries'],source_manifest=str(a.source_manifest.resolve()),
        source_manifest_sha256=sha(a.source_manifest),input_sha256=files,
        study_source_sha256=sha(Path(__file__)),auditor_sha256=sha(ROOT/'python/moe_ub_cosim/legacy_audit.py'),
        network_executed=False,cases=['normal','local','prefix'])
    save(out/'frozen.json',frozen)
    print(json.dumps(dict(prepared=True,requests=15,choices=120,network_executed=False)))


def verify(study):
    frozen=load(study/('frozen-v2.json' if (study/'frozen-v2.json').exists() else 'frozen.json'))
    assert sha(Path(__file__))==frozen['study_source_sha256']
    assert sha(ROOT/'python/moe_ub_cosim/legacy_audit.py')==frozen['auditor_sha256']
    assert sha(frozen['backend'])==frozen['backend_sha256']
    for path,expected in frozen['input_sha256'].items():assert sha(study/path)==expected,path
    for path,expected in frozen['compiled_sources'].items():assert sha(ROOT/path)==expected,path
    for path in frozen['linked_libraries']:assert sha(path)==frozen['library_hashes'][Path(path).name]
    return frozen


def run(a):
    study=a.study.resolve();frozen=verify(study)
    assert a.case in frozen['cases'] and not (study/('run-'+a.case)).exists()
    protocol=frozen.get('protocol_files',{}).get(a.case,a.case+'.json')
    log_name='run-'+a.case+('-corrected' if protocol!=a.case+'.json' else '')
    cli(study,log_name,['deepep-run','--plan',study/'plan','--binding',study/'binding.json',
        '--topology',study/'topology','--protocol',study/protocol,'--transport',study/'transport.json',
        '--backend',frozen['backend'],'--output',study/('run-'+a.case)])


def analyze(a):
    study=a.study.resolve();frozen=verify(study);out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    assert sha(frozen['source_manifest'])==frozen['source_manifest_sha256']
    _,tokens,records,counts=read_selection(frozen['source_manifest'])
    assert tokens==load(study/'plan-input.json')['calls'][0]['tokens'] and records==load(study/'selection.json')['records']
    summaries={};receipts={};execution_sha=None
    for case in frozen['cases']:
        run=study/('run-'+case);protocol=study/frozen.get('protocol_files',{}).get(case,case+'.json');audit_path=out/(case+'-audit.json')
        cli(out,'audit-'+case,['deepep-audit','--run',run,'--protocol',protocol,'--output',audit_path])
        summary=load(audit_path);assert summary['passed'] and not summary['route_source_verified']
        actual=sha(run/'execution.json')
        if execution_sha is None:execution_sha=actual
        assert actual==execution_sha
        work=load(run/'execution.json');result=load(run/'result.json')
        selected=Counter((rank,expert) for rank,rank_tokens in enumerate(tokens) for choices in rank_tokens for expert in choices)
        dispatch=Counter((op[4],op[6]) for op in work['operations'] if op[2]==0 and op[3]=='data')
        assert selected==dispatch
        assert sum(1 for op in work['operations'] if op[3]!='data')==16384
        if case!='normal':assert result['link_failures']['pending_events']==0
        summary.update(expert_selection_matches_local_sources=True,
            per_rank_kernel_completions_ns=[max(t for e,t in zip(work['events'],result['legacy']['event_times_ns'])
                                              if e[0]=='kernel_complete' and e[1]==rank) for rank in range(32)],
            reference_events=len(result['legacy']['transport']['dcqcn_reference']['events']))
        summaries[case]=summary;receipts[case]=dict(raw_result_sha256=sha(run/'result.json'),audit_sha256=sha(audit_path))
        del result,work
    normal,local,prefix=[summaries[case]['last_kernel_complete_ns'] for case in frozen['cases']]
    evidence=dict(passed=True,network_endpoints=128,ep_size=32,data_active_ranks=15,requests=15,selected_tokens=15,
        selected_choices=120,selected_layer=3,source_entry=1,remote_revision_verified=False,
        routing_matches_local_source=True,collector_precision='awq_4bit',functional_payload_format='bf16',
        expert_counts=dict(sorted(counts.items())),distinct_selected_experts=len(counts),max_expert_choices=max(counts.values()),
        conditions=summaries,receipts=receipts,normal_task_ns=normal,local_task_ns=local,prefix_task_ns=prefix,
        local_minus_normal_ns=local-normal,prefix_minus_normal_ns=prefix-normal,
        prefix_improvement_vs_local_ns=local-prefix,
        prefix_relative_improvement_vs_local=(local-prefix)/local,
        recovered_local_extra_wait_fraction=(local-prefix)/(local-normal) if local>normal else None,
        algorithm_innovation_validated=False,full_inference_executed=False,physical_gpu_executed=False,
        source_manifest_sha256=frozen['source_manifest_sha256'],
        frozen_sha256=sha(study/('frozen-v2.json' if (study/'frozen-v2.json').exists() else 'frozen.json')),
        scope='15 recorded expert selections with declared rank mapping and functional old-library communication; one EP32 group, not full-cluster load')
    save(out/'analysis.json',evidence)
    print(json.dumps({key:evidence[key] for key in ('passed','normal_task_ns','local_task_ns','prefix_task_ns','prefix_improvement_vs_local_ns')}))


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    prepare_parser=sub.add_parser('prepare');prepare_parser.add_argument('--source-manifest',type=Path,required=True)
    prepare_parser.add_argument('--build',type=Path,required=True);prepare_parser.add_argument('--output',type=Path,required=True)
    run_parser=sub.add_parser('run');run_parser.add_argument('--study',type=Path,required=True)
    run_parser.add_argument('--case',choices=('normal','local','prefix'),required=True)
    analysis=sub.add_parser('analyze');analysis.add_argument('--study',type=Path,required=True);analysis.add_argument('--output',type=Path,required=True)
    a=p.parse_args();{'prepare':prepare,'run':run,'analyze':analyze}[a.action](a)


if __name__=='__main__':main()
