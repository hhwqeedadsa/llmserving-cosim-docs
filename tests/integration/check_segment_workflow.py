"""Exercise finite original blocks, passive facts, and relocated backend replay."""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys

from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.research_storage import stored_rows
from moe_ub_cosim.user_io import read,save


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--backend',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True);args=p.parse_args()
    root=Path(__file__).resolve().parents[2];out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    binary=args.backend.resolve();commands=[];example=root/'examples/a3_segment'
    def cli(label,*words,fail=False,offline=False):
        cmd=[sys.executable,*(['-S'] if offline else []),'-m','moe_ub_cosim',*map(str,words)]
        result=subprocess.run(cmd,cwd=root,capture_output=True,text=True,timeout=600)
        for stream in ('stdout','stderr'):(out/f'{label}.{stream}.log').write_text(getattr(result,stream))
        commands.append(dict(command=cmd,cwd=str(root),exit_code=result.returncode,ended_utc=datetime.now(timezone.utc).isoformat(),log_prefix=str(out/label)))
        save(out/'commands.json',commands)
        assert (result.returncode!=0 if fail else result.returncode==0),(label,result.stderr)
        return result
    cli('resolve','resolve','--config',example/'scenario.yaml','--backend',binary,'--output',out/'resolved')
    shutil.copytree(example,out/'example')
    source=read(out/'example/source-run.json');source['backend']=str(binary)
    source['inputs']={k:str(out/'resolved'/v) for k,v in dict(service='service.json',layer='layer.json',system='system.json',network='network').items()}
    save(out/'example/source-run.json',source)
    cli('prepare','segment','prepare','--config',out/'example/segment.json','--output',out/'prepared')
    prepared=read(out/'prepared/service.json');assert prepared['schema_version']=='moe-ub-configured-service/5'
    assert prepared['groups']==read(out/'resolved/service.json')['groups']
    cli('run','run','--config',out/'prepared/run.json','--output',out/'run')
    cli('validate','validate','--run',out/'run',offline=True)
    # Backend also rejects initial requests outside the complete deployment,
    # independently of the Python input validator.
    invalid=out/'invalid-deployment';shutil.copytree(out/'run/raw',invalid)
    declaration=read(invalid/'deployment.json');request=deepcopy(declaration['groups'][0]['segment']['requests'][0])
    request.update(request_id='foreign',rank_id=999)
    declaration['groups'][0]['segment']['requests'].append(request);save(invalid/'deployment.json',declaration)
    cmd=list(map(str,[binary,prepared['run_id'],invalid,out/'run/inputs/system.json',out/'run/inputs/network',
        out/'run/inputs/backend-observation.json',out/'rejected-execution',1000000]))
    result=subprocess.run(cmd,cwd=root,capture_output=True,text=True,timeout=30)
    (out/'invalid-deployment.stderr.log').write_text(result.stderr)
    commands.append(dict(command=cmd,cwd=str(root),exit_code=result.returncode,log_prefix=str(out/'invalid-deployment')))
    save(out/'commands.json',commands)
    assert result.returncode==1 and 'belongs to no declared rank' in result.stderr,result.stderr
    before=read(out/'run/manifest.json')['artifacts']
    cli('facts','facts','--run',out/'run','--config',example/'facts.json','--output',out/'facts',offline=True)
    cli('validate-after-facts','validate','--run',out/'run',offline=True)
    assert read(out/'run/manifest.json')['artifacts']==before
    detail=read(out/'facts/facts.json');assert detail['passed'] and not (out/'facts/observed').exists()
    nodes=list(stored_rows(out/'facts/truth','nodes.jsonl'));deliveries=list(stored_rows(out/'facts/truth','deliveries.jsonl'))
    assert nodes and deliveries and all(n['layer_id'] in (2,3) for n in nodes)
    assert all(d['business_ready_ns'] is None for d in deliveries if d['message_role']=='status')
    assert all(d['application_metadata_ready_ns'] is not None for d in deliveries if d['message_role']=='status')
    assert any(d['local_read_complete_ns']<d['protocol_retired_ns'] for d in deliveries)
    for kind in ('bad-coverage','overflow'):
        config=read(example/'facts.json')
        if kind=='bad-coverage':config['selection']['layer_ids']=[99]
        else:config['limits']['max_nodes']=1
        save(out/(kind+'.json'),config)
        cli(kind,'facts','--run',out/'run','--config',out/(kind+'.json'),'--output',out/kind,fail=True,offline=True)
        assert not (out/kind/'facts.json').exists()
    cli('reject-old-freeze','slice','create','--from-run',out/'run','--output',out/'wrong-slice',fail=True,offline=True)
    assert not (out/'wrong-slice').exists()
    cli('freeze','segment','freeze','--from-run',out/'run','--output',out/'slice',offline=True)
    (out/'slice').rename(out/'relocated-slice');bundle=out/'relocated-slice'
    (out/'run').rename(out/'hidden-run');(out/'prepared').rename(out/'hidden-prepared');(out/'resolved').rename(out/'hidden-resolved')
    cli('inspect','slice','inspect','--slice',bundle,'--verify',offline=True)
    cli('sample','sample','--slice',bundle,'--config',example/'sample.json','--output',out/'sample',offline=True)
    cli('replay','replay','--slice',bundle,'--config',example/'replay.json','--backend',binary,'--output',out/'replay',offline=True)
    cli('validate-replay','validate','--run',out/'replay',offline=True)
    original=out/'hidden-run/raw/execution';replayed=out/'replay/raw/execution';a=read(original/'result.json');b=read(replayed/'result.json')
    compared={}
    for field in a['record_storage']['fields']:
        rows=list(iter_result(original,field,a));assert rows==list(iter_result(replayed,field,b)),field;compared[field]=len(rows)
    assert a['simulation_end_ns']==b['simulation_end_ns']
    assert read(out/'replay/manifest.json')['scope']=='dependency_driven_frozen_finite_segment'
    plan=read(out/'hidden-run/raw/graphs/g0/plan.json');assert plan['executed_layer_ids']==[2,3]
    front=read(out/'hidden-run/raw/service_result.json');assert front['completion_scope']=='selected_segment_only'
    assert all(not r['request_blocks'] and r['kv_used_bytes']==0 for r in front['memory_final'])
    assert all(row[k]==0 for row in a['rank_resources'] for k in ('compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks'))
    save(out/'summary.json',dict(passed=True,simulation_end_ns=a['simulation_end_ns'],selected_layers=[2,3],facts=dict(nodes=len(nodes),deliveries=len(deliveries)),replay_compared_fields=compared,
        source_state='fresh_network_ready_block_input_and_KV',full_capacity_preserved=True,offline_replay=True))
    print('PASS: finite block scope, bounded internal facts, original layer identity, full capacity, and relocated offline replay')


if __name__=='__main__':main()
