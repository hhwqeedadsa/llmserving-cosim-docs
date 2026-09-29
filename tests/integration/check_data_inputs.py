"""Staged Mixtral metadata, continuous routes and partial H100 rows on actual UB."""
import argparse
from copy import deepcopy
import datetime
import json
from pathlib import Path
import subprocess
import sys

from check_delay import read, sha
from check_service_boundaries import check_sparse_graph
from check_variants import rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(); root=Path(__file__).resolve().parents[2]
    out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    fixture=root/'fixtures/m4_data_inputs';commands=[];results={};failures={}
    def invoke(label,c,provenance=True):
        source=out/(label+'.input.json');source.write_text(json.dumps(c,indent=2)+'\n')
        argv=[sys.executable,'-m','moe_ub_cosim.service','--input',str(source),
            '--layer',str(root/'fixtures/m2_single_layer/layer.json'),'--backend',str(binary),
            '--system',str(root/'fixtures/m1_compute/system.json'),
            '--network',str(root/'fixtures/m2_single_layer/network'),'--output-dir',str(out/label),
            '--max-simulation-ns','5000000']
        if provenance is not None:
            obs=read(root/'fixtures/m2_single_layer/observation.json')
            obs.update(window_end_ns=1000000,record_provenance=provenance)
            path=out/(label+'.observation.json');path.write_text(json.dumps(obs)+'\n')
            argv+=['--observation',str(path)]
        started=datetime.datetime.now(datetime.timezone.utc).isoformat()
        result=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=90)
        for stream in ('stdout','stderr'):(out/(label+'.'+stream+'.log')).write_text(getattr(result,stream))
        commands.append(dict(label=label,argv=argv,cwd=str(root),exit_code=result.returncode,
            started_utc=started,finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        return result
    def run(label,c,provenance=True):
        result=invoke(label,c,provenance);assert result.returncode==0,(label,result.stderr)
        path=out/label;front=read(path/'service_result.json');report=read(path/'execution/result.json')
        manifest=read(path/'manifest.json');evidence=read(path/'input_evidence.json')
        assert manifest['normal_exit'] and manifest['backend_exit_code']==0 and report['normal_exit']
        assert manifest['model_source']=='locked_actual_model_config' and manifest['route_data_is_synthetic']
        assert not manifest['target_profile_available'] and not manifest['full_model_execution_validated']
        assert manifest['calibration_status']==manifest['communication_calibration_status']=='unvalidated_reference'
        assert front['service_finished'] and front['finished_ns']==report['service_finished_ns']<5000000
        assert front['route_records_consumed']==front['route_records_expected']==12
        assert front['scheduler_calls']==12 and front['decisions']==4 and len(front['waves'])==3
        assert front['waiting']==front['pending']==front['running']==front['inflight']==[0]*4
        assert report['network_initializations']==1 and report['csv_completed_tasks']==0
        assert report['active_wqes']==[0]*4 and report['network_drain']['idle']
        assert all(b['same_network_objects'] for b in report['boundary_resources'])
        assert all(r['finished'] and r['remaining_nodes']==r['stale_callbacks']==r['send_slots']==r['compute_slots']==0
                   for r in report['rank_resources'])
        audit=evidence['model'];reserved=25759850496+2**30+2**20
        assert audit['weight_bytes_per_rank']==25759850496 and audit['kv_block_bytes']==2097152
        assert len(audit['layer_structure'])==32 and audit['executed_layer_ids']==[7]
        assert all(r['kv_capacity_blocks']==28164 and r['kv_used_bytes']==0 and r['used_bytes']==reserved
                   for r in front['memory_final'])
        for event in front['memory_events']:
            for r in event['ranks']:
                assert r['used_bytes']==reserved+r['kv_used_bytes']<=80*2**30
                assert r['kv_used_bytes']%audit['kv_block_bytes']==0
        assert max(r['kv_used_bytes'] for e in front['memory_events'] for r in e['ranks'])==2*audit['kv_block_bytes']
        normalized={(r['request_id'],r['token_index'],r['layer_id']):r for r in evidence['normalized_routes']}
        plans=[];before=0;consumed=set()
        for step,w in enumerate(front['waves']):
            p=read(path/f"graphs/{w['graph_id']}/plan.json");plans.append(p)
            assert p['ready_ns']==w['submitted_ns']==before
            check_sparse_graph(p,report,path/'execution')
            assert p['input']['layer_id']==7 and p['input']['tokens']==w['tokens']
            assert w['finished_ns']==max(e['time_ns'] for e in report['graph_completions'] if e['graph_id']==w['graph_id'])
            for token,source in zip(p['input']['tokens'],p['input_provenance']['route_records']):
                key=token['request_id'],token['token_index'],token['layer_id']
                assert key not in consumed and token['token_index']==step
                consumed.add(key);assert source==normalized[key]
                assert source['expert_ids']==token['expert_ids']
                request=next(r for r in c['requests'] if str(r['request_id'])==token['request_id'])
                assert source['source_token_index']==request['prompt_tokens']+step
                assert source['source_line']==source['source_index']-99
            assert p['compute_source']==evidence['compute']['kind']
            before=w['finished_ns']
        assert consumed==set(normalized)
        for r in front['requests']:
            assert r['status']=='FINISHED' and r['num_tokens_reached']==r['target_tokens'] and r['preemptions']==0
            assert r['completed_segment_steps']==r['next_route_decode_index']==3
            assert len(r['intertoken_latencies_ns'])==3 and sum(r['intertoken_latencies_ns'])==front['finished_ns']
        transfers=[t for p in plans for t in p['transfers']]
        for field in ('transfer_id','ub_task_id','comm_tag'):assert len({t[field] for t in transfers})==len(transfers)
        assert len(transfers)==len(report['transfers'])==len(report['target_writes'])==len(report['wqe_starts'])
        assert sum(t['payload_bytes'] for t in transfers)==sum(t['bytes'] for t in report['transfers'])
        native={str(p.relative_to(path/'execution/network/runlog')):sha(p)
                for p in (path/'execution/network/runlog').rglob('*') if p.is_file()}
        value=dict(front=front,report=report,plans=plans,evidence=evidence,protocol=rows(path/'protocol.jsonl'),native=native)
        if provenance is not None:
            value['observations']={p.name:sha(p) for p in (path/'execution/observed').iterdir()}
        results[label]=value;return value
    model=read(fixture/'model-stage.json');route=read(fixture/'route-stage.json');compute=read(fixture/'compute-stage.json')
    a=run('model',model);b=run('route',route);c=run('compute',compute)
    for label,provenance in [('off',False),('absent',None),('repeat',True)]:
        result=run(label,compute,provenance)
        for key in ('front','report','plans','evidence','protocol','native'):assert result[key]==c[key],(label,key)
        if provenance is not None:assert result['observations']==c['observations']
    assert a['evidence']['model']==b['evidence']['model']==c['evidence']['model']
    assert a['evidence']['compute']==b['evidence']['compute']
    assert b['evidence']['route_source']==c['evidence']['route_source']
    assert b['evidence']['normalized_routes']==c['evidence']['normalized_routes']
    assert a['plans'][0]['transfers']==b['plans'][0]['transfers']
    assert a['plans'][1]['transfers']!=b['plans'][1]['transfers']
    for p,q in zip(b['plans'],c['plans']):
        assert p['transfers']==q['transfers'] and p['operations']==q['operations']
        assert p['input']['tokens']==q['input']['tokens']
        for g,h in zip(p['rank_graphs'],q['rank_graphs']):
            for n,m in zip(g['nodes'],h['nodes']):
                left,right=deepcopy(n),deepcopy(m)
                if n['name']=='attention-route-pack':
                    assert m['duration_ns']-n['duration_ns']==82911
                    left.pop('duration_ns');right.pop('duration_ns')
                assert left==right
    assert c['front']['finished_ns']-b['front']['finished_ns']==3*82911
    bad=[]
    def case(name,message,change):
        value=deepcopy(compute);change(value);bad.append((name,message,value))
    case('digest','SHA256',lambda x:x['routes']['file'].update(sha256='0'*64))
    case('dtype','float16',lambda x:x.update(dtype='bfloat16'))
    case('capacity','one KV block',lambda x:x['memory'].update(device_bytes=20*2**30))
    case('route-count','Unexpected/duplicate',lambda x:x['requests'][0].update(decode_steps=2))
    case('wrong-model','route source',lambda x:x['routes'].update(model='unmatched'))
    case('small-buffer','buffer',lambda x:x['memory'].update(communication_buffer_bytes=8192))
    for name,message,config in bad:
        result=invoke('bad-'+name,config)
        assert result.returncode!=0 and message in result.stderr,(name,result.stderr)
        assert not (out/('bad-'+name)/'execution').exists()
        failures[name]=dict(exit_code=result.returncode,expected_error=message)
    sources=list((root/'python/moe_ub_cosim').glob('*.py'))+[Path(__file__).resolve(),root/'tests/unit/data_inputs_test.py']
    sources += [p for p in fixture.iterdir() if p.suffix in ('.json','.jsonl')]
    summary=dict(passed=True,normal_runs=list(results),expected_failures=failures,binary_sha256=sha(binary),
        source_hashes={str(p.relative_to(root)):sha(p) for p in sources},
        metrics={k:dict(finished_ns=v['front']['finished_ns'],writes=len(v['report']['transfers']),
            business_bytes=sum(t['bytes'] for t in v['report']['transfers'])) for k,v in results.items()},
        actual_model_config_validated=True,real_route_data_available=False,target_profile_available=False,
        full_model_execution_validated=False)
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
