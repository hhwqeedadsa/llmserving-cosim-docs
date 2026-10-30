"""Independent raw-choice and lifecycle audit of two already executed runs."""
import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from moe_ub_cosim.scaleout_verify import node_events


def load(path):return json.loads(path.read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')


def independent(run):
    c=load(run/'input.json');r=load(run/'result.json');f=load(run/'frontend.json')
    assert r['passed'] and r['gpu_count']==128
    manifest=load(run/c['route_replay']['manifest']['path'])
    originals={};source_metadata={}
    for source in manifest['files']:
        path=run/source['file']['path']
        assert sha(path)==source['file']['sha256']
        originals[source['file_id']]=load(path)
        source_metadata[source['file_id']]=source
    actual=[json.loads(line) for line in (run/'contribution_calls.jsonl').read_text().splitlines()]
    by_key={(row['binding']['graph_id'],row['binding']['source_rank'],row['binding']['slot'],row['binding']['logical_layer']):row for row in actual}
    assert len(by_key)==len(actual)==16
    events=node_events(run)
    callbacks={row['id']:row for row in r['service']['message_callbacks']}
    history={row['graph_id']:row for row in f['history']}
    retire={row['graph_id']:row['finished_ns'] for row in r['service']['waves']}
    per_call=[];bundle_count=contributions=0
    refs=[]
    for graph,entry in history.items():
        group=c['parallel']['groups'][entry['group_index']]
        wave=load(run/'graphs'/graph/'wave.json')
        names={(int(rank),n['name']):n['id'] for rank,ns in wave['rank_nodes'].items() for n in ns}
        messages={(m['layer'],m['phase'],m['src'],m['dst']):m['id'] for m in wave['messages']}
        def time(rank,node,kind):return events[graph,rank,node,kind]
        for request in c['route_replay']['requests']:
            src=request['rank_id']
            if src not in group:continue
            source_entry=request['first_decode_entry']+entry['step']
            for lid,source_layer in enumerate(c['route_replay']['layer_ids']):
                selected=originals[request['file_id']][source_entry][str(source_layer)][0]
                record=by_key[graph,src,request['slot'],lid]
                spec,state,binding=record['spec'],record['state'],record['binding']
                assert sorted(selected)==spec['expected_experts']==state['accepted_experts'] and not state['missing_experts']
                inv=spec['invocation']
                meta=source_metadata[request['file_id']]
                declared_request=dict(file_id=request['file_id'],sha256=meta['file']['sha256'],
                    request_id=src*c['workload']['batch_per_rank']+request['slot'],original_path=meta['original_path'])
                key=hashlib.sha256(json.dumps(declared_request,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
                assert inv['request_id']=='request.'+key
                assert binding['source']==dict(request_id=declared_request['request_id'],file_id=request['file_id'],
                    source_entry=source_entry,source_token_row=0,source_layer_id=source_layer,sha256=meta['file']['sha256'])
                assert 'input_sha256' not in inv and inv['input_identity']['kind']=='simulation_lineage/1'
                assert inv['source_layer']==source_layer and inv['decode_step']==entry['step']
                assert inv['token_coordinate']==dict(kind='route_decode_entry/1',source_entry=source_entry,source_token_row=0)
                owners=defaultdict(list)
                placement=c['route_replay'].get('expert_to_group_rank')
                for expert in selected:
                    owner=group[placement[lid][expert] if placement is not None else expert//(c['model']['experts']//len(group))]
                    owners[owner].append(expert)
                bundles={b['rank']:b for b in binding['bundles']}
                assert set(bundles)==set(owners)
                assert set(state['attempts'])=={'original.r'+str(rank) for rank in owners}
                pack=names[src,f'L{lid}.pack']
                assert inv['input_identity']['producer_rank']==src and inv['input_identity']['producer_node']==pack
                results=[]
                for rank,experts in owners.items():
                    bound=bundles[rank];bundle=f'r{rank}';attempt=state['attempts']['original.'+bundle]
                    assert bound['experts']==sorted(experts)
                    assert next(b['experts'] for b in spec['bundles'] if b['id']==bundle)==sorted(experts)
                    expert_node=names[rank,f'L{lid}.experts'];producer=names[rank,f'L{lid}.combine_reduce']
                    assert bound['expert_node']==expert_node and bound['result_producer_node']==producer
                    assert attempt['started_ns']==time(src,pack,'issue')
                    assert attempt['compute_begin_ns']==time(rank,expert_node,'issue')
                    assert attempt['compute_done_ns']==time(rank,expert_node,'callback')
                    assert attempt['retired_ns']==retire[graph] and not attempt['resources']
                    if src==rank:
                        assert bound['dispatch_message'] is None and bound['combine_message'] is None
                        ready=time(src,pack,'callback');result=time(rank,producer,'callback')
                    else:
                        dispatch=messages[lid,'dispatch',src,rank];combine=messages[lid,'combine',rank,src]
                        assert (bound['dispatch_message'],bound['combine_message'])==(dispatch,combine)
                        ready=callbacks[dispatch]['recv_callback_ns'];result=callbacks[combine]['recv_callback_ns']
                        for phase,message in [('dispatch',dispatch),('combine',combine)]:
                            resource=state['resources'][f'{phase}.{message}.{bundle}']
                            assert resource['opened_ns']==callbacks[message]['send_post_ns']
                            assert resource['closed_ns']==callbacks[message]['send_callback_ns']
                            refs.append(dict(call=record['call'],phase=phase,message=message,
                                             receiver_ns=callbacks[message]['recv_callback_ns'],source_ack_ns=resource['closed_ns']))
                    assert attempt['input_ready_ns']==ready and attempt['result_ready_ns']==result
                    assert ready<=attempt['compute_begin_ns']<=attempt['compute_done_ns']<=result<=retire[graph]
                    graph_ref=state['resources'][f'graph.{graph}.{bundle}']
                    assert graph_ref['opened_ns']==time(src,pack,'issue') and graph_ref['closed_ns']==retire[graph]
                    assert state['winners'][bundle]==dict(attempt='original.'+bundle,time_ns=result)
                    results.append(result);bundle_count+=1;contributions+=len(experts)
                assert state['logical_complete_ns']==max(results) and state['declared_call_retirable']
                assert state['all_started_attempts_retired']
                per_call.append(dict(call=record['call'],graph_id=graph,source_rank=src,logical_complete_ns=max(results),
                    retired_ns=retire[graph],expert_count=len(selected),bundles=len(owners)))
    assert len(per_call)==16 and bundle_count==32 and contributions==128
    summary=load(run/'contribution_summary.json')
    assert summary['invocations']==summary['retired_calls']==summary['logical_complete_calls']==16
    assert summary['bundles']==32 and summary['lifecycle_events']==summary['reserved_lifecycle_events']==380
    assert summary['service_drained_ns']==r['finish_ns']
    assert len(r['service_transport']['messages'])==62
    message=next(m for m in r['service_transport']['messages'] if m['src']==0 and m['dst']==6 and m['layer']==0 and m['phase']=='dispatch')
    capture=r['packet_capture'];columns=capture['columns']
    tx=[]
    for raw in capture['events']:
        event=dict(zip(columns,raw))
        if (event['event']==capture['event_codes']['tx'] and event['node']==message['src'] and
            event['ip_protocol']==capture['protocol_codes']['data'] and event['message_id']==message['stream'] and
            message['begin']<=event['sequence']<message['end']):
            tx.append(dict(time_ns=event['time_ns'],sequence=event['sequence'],payload_bytes=event['data_payload_bytes']))
    assert tx and tx[0]['time_ns']==callbacks[message['id']]['send_post_ns']
    return dict(application_finished_ns=max(row['finished_ns'] for row in r['service']['waves']),network_drained_ns=r['finish_ns'],
                invocations=16,bundles=32,expert_contributions=128,lifecycle_events=380,
                packet_events=len(r['packet_capture']['events']),fault_drops=r['fault_drops'],calls=per_call,message_references=refs,
                target_dispatch=dict(message_id=message['id'],stream_id=message['stream'],source_transmissions=tx))


def main(args):
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    normal,local=map(Path.resolve,(args.normal,args.local))
    result={case:independent(path) for case,path in [('normal',normal),('local',local)]}
    normal_native=load(normal/'result.json');reference=load(args.reference/'result.json')
    assert normal_native==reference
    for path in (normal/'graphs').rglob('*'):
        if path.is_file():assert path.read_bytes()==(args.reference/'graphs'/path.relative_to(normal/'graphs')).read_bytes()
    assert (normal/'execution_events.jsonl').read_bytes()==(args.reference/'execution_events.jsonl').read_bytes()
    assert not result['normal']['fault_drops']
    assert result['local']['fault_drops'] and result['local']['application_finished_ns']>result['normal']['application_finished_ns']
    assert load(local/'rdma.json')['data_loss']==dict(kind='periodic_destination_data/1',numerator=1,denominator=1,
        offset=0,start_ns=0,end_ns=20000,nodes=[6])
    commands=[]
    def cli(argv,expect):
        run=subprocess.run([sys.executable,'-B','-m','moe_ub_cosim','scaleout',*map(str,argv)],capture_output=True,text=True,timeout=30)
        commands.append(dict(argv=argv,exit_code=run.returncode,stdout=run.stdout,stderr=run.stderr))
        assert run.returncode==expect,run.stderr
        return run
    relocated=output/'relocated';shutil.copytree(normal,relocated)
    command=['rdma-check','--run',str(relocated)]
    cli(command,0)
    original=(relocated/'contribution_calls.jsonl').read_bytes()
    original_summary=(relocated/'contribution_summary.json').read_bytes()
    rejected={}
    for mutation in ['wrong-source','missing-expert','early-result','early-release','missing-call','wrong-summary']:
        rows=[json.loads(line) for line in original.splitlines()]
        target=rows[0]
        if mutation=='wrong-source':target['spec']['invocation']['request_id']='foreign-request'
        elif mutation=='missing-expert':target['state']['accepted_experts'].pop()
        elif mutation=='early-result':next(iter(target['state']['attempts'].values()))['result_ready_ns']=0
        elif mutation=='early-release':next(iter(target['state']['resources'].values()))['closed_ns']=0
        elif mutation=='missing-call':rows.pop()
        else:
            summary=json.loads(original_summary);summary['retired_calls']-=1
            (relocated/'contribution_summary.json').write_text(json.dumps(summary))
        (relocated/'contribution_calls.jsonl').write_text(''.join(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n' for row in rows))
        failed=cli(command,1)
        expected='contribution binding summary' if mutation=='wrong-summary' else 'bound contribution differs'
        assert expected in failed.stderr+failed.stdout
        rejected[mutation]=True
        (relocated/'contribution_summary.json').write_bytes(original_summary)
    (relocated/'contribution_calls.jsonl').write_bytes(original)
    bad=load(args.inputs/'service-tracked.json');bad['contribution_tracking']['max_invocations']=15
    save(output/'insufficient.json',bad)
    not_started=output/'must-not-start'
    backend=json.loads((normal/'execution/probe.json').read_bytes())['command']
    executable=next(p for p in backend if str(p).endswith('ns3.42-moe-rdma-service')) if any(str(p).endswith('ns3.42-moe-rdma-service') for p in backend) else None
    # Backend may be behind the internal worker; its exact executable path is
    # present in that worker argv and is required only after validation.
    assert executable
    failure=cli(['rdma-run','--config',str(output/'insufficient.json'),'--protocol',str(args.inputs/'rdma.json'),
        '--topology',str(args.inputs/'topology'),'--backend',executable,'--protoc','/unused-after-preflight',
        '--output',str(not_started)],1)
    assert 'cumulative budget' in failure.stderr+failure.stdout and not not_started.exists()
    report=dict(passed=True,execution_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        cases=result,reference_unchanged_normal_native_graphs_and_events=True,raw_sources_independently_checked=True,
        negative_checks=rejected,cumulative_budget_rejected_before_output=True,relocation_passed=True,
        new_network_executions=0,scope='original-attempt binding correctness; fault cost is not optimization gain')
    save(output/'verification.json',report);save(output/'commands.json',commands)
    print(json.dumps(dict(passed=True,invocations_per_case=16,bundles_per_case=32,negative_checks=len(rejected),
                         normal_ns=result['normal']['application_finished_ns'],local_ns=result['local']['application_finished_ns'])))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['normal','local','reference','inputs','output']:parser.add_argument('--'+name,type=Path,required=True)
    main(parser.parse_args())
