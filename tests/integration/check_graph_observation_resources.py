"""Explicit graph budgets and streamed observation on real UB, with RSS evidence."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys

from moe_ub_cosim.observation import backend_observation, iter_json_lines, validate_observed
from moe_ub_cosim.research_export import export_records, write_export
from moe_ub_cosim.resources import actual_usage, cann_usage, check_graph, check_run


def read(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n');return path
def size(path):return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2];out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    base=read(root/'fixtures/m10_full_model/service.json');layers=read(root/'fixtures/m10_full_model/layers.json')
    budget=read(root/'fixtures/m10_resources/graph-budget.json');obudget=read(root/'fixtures/m10_resources/observation-budget.json')
    base['execution']['resource_budget']=budget;base['groups']=base['groups'][:1];base['requests']=base['requests'][:1]
    # Two native iterations, each with 6 cross-chip expert contributions split into 4 B writes.
    base['requests'][0]['decode_routes']=[{str(i):[4,5] for i in (1,2,3)} for _ in range(2)]
    layers['groups']=layers['groups'][:1]
    for layer in layers['groups'][0]['layers']:layer['chunk_bytes']=4
    ranks=layers['groups'][0]['layers'][0]['rank_ids'];max_ns=6000123
    network=root/'fixtures/m3_shared_bottleneck/network';system=root/'fixtures/m1_compute/system.json'
    observation=dict(clocks=[dict(device_type='rank',device_id=r,offset_ns=-13*r,drift_ppb=10*r) for r in ranks]+
        [dict(device_type='switch',device_id=s,offset_ns=37,drift_ppb=1000) for s in (8,9)],
        switches=[dict(switch_id=s,port_ids=[4],start_local_ns=37,end_local_ns=max_ns,period_ns=2000) for s in (8,9)],
        record_provenance=True)
    commands=[];cases={};failures={}
    # Count-only lower bound: full target status traffic, no Token vectors or full graph allocation.
    status=deepcopy(layers['groups'][0]['layers'][0]);status.update(rank_ids=list(range(128)),
        expert_placement=[r for r in range(128) for _ in range(2)],hidden_dim=7168,chunk_bytes=8192,tokens=[])
    status['deployment']['ranks']=[dict(rank_id=r,chip_id=str(r//2)) for r in range(128)]
    counted=cann_usage(status)
    assert counted==dict(transfers=32512,fabric_transfers=32256,local_transfers=256,nodes=65152)
    lower_bound={k:58*v for k,v in counted.items()}
    try:check_graph(lower_bound,budget)
    except ValueError:pass
    else:raise AssertionError('target count-only lower bound must exceed fixture budget')
    save(out/'target-count-only.json',dict(status_only=True,target_scale_executed=False,tokens=0,
        per_layer=counted,moe_layers=58,total=lower_bound,fixture_budget_rejected=True,
        caveat='Counts only; excludes expert vectors, Combine, model stages, plans, host RAM and execution evidence'))

    def invoke(label,argv,code=0,stdin=None):
        argv=[str(v) for v in argv];start=datetime.now(timezone.utc).isoformat()
        # A fresh wrapper isolates wait4/RUSAGE_CHILDREN's maximum for each command.
        # This includes the backend grandchild peak inherited through Python's wait.
        meter="""import json,resource,subprocess,sys,time
start=time.monotonic()
p=subprocess.run(sys.argv[2:])
with open(sys.argv[1],'w') as stream:
 json.dump(dict(wall_seconds=time.monotonic()-start,max_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss),stream)
sys.exit(p.returncode)
"""
        measured=[sys.executable,'-c',meter,str(out/f'{label}.time.json'),*argv]
        proc=subprocess.run(measured,cwd=root,input=stdin,capture_output=True,text=True,timeout=300)
        for stream in ('stdout','stderr'):(out/f'{label}.{stream}.log').write_text(getattr(proc,stream))
        if stdin is not None:(out/f'{label}.stdin.log').write_text(stdin)
        times=read(out/f'{label}.time.json');rss=times['max_rss_kib'];elapsed=times['wall_seconds']
        commands.append(dict(label=label,argv=measured,cwd=str(root),started_utc=start,ended_utc=datetime.now(timezone.utc).isoformat(),
            exit_code=proc.returncode,max_rss_kib=rss,wall_time=elapsed))
        save(out/'commands.json',commands)
        assert (proc.returncode==0 if code==0 else proc.returncode!=0),(label,proc.returncode,proc.stderr)
        return proc

    def service(label,streamed=True,provenance=True,c=None,l=None,b=None,expect=None):
        c=deepcopy(base if c is None else c);l=deepcopy(layers if l is None else l)
        obs=deepcopy(observation);obs['record_provenance']=provenance
        if streamed:obs['resource_budget']=deepcopy(obudget if b is None else b)
        raw=backend_observation(obs,ranks,max_ns)
        if streamed:
            # Exercise the sparse native counter itself beyond 4096 windows.
            for port in raw['ports']:port['window_period_ns']=1000
        cp=save(out/f'{label}.service.json',c);lp=save(out/f'{label}.layers.json',l)
        op=save(out/f'{label}.raw-observation.json',raw);save(out/f'{label}.observation.json',obs)
        argv=[sys.executable,'-m','moe_ub_cosim.service','--input',cp,'--layer',lp,'--backend',binary,'--system',system,
            '--network',network,'--output-dir',out/label,'--max-simulation-ns',max_ns,'--observation',op]
        proc=invoke(label,argv,1 if expect else 0);path=out/label
        if expect:
            err=path/'backend.stderr.log';diagnostic=proc.stderr+(err.read_text() if err.exists() else '')
            assert expect in diagnostic,(label,diagnostic);failures[label]=expect
            assert not (path/'execution/result.json').exists();return path
        front=read(path/'service_result.json');report=read(path/'execution/result.json')
        assert front['service_finished'] and len(front['waves'])==2 and report['normal_exit'] and report['network_drain']['idle']
        assert report['network_initializations']==1 and front['graph_resources']==report['graph_resources']
        assert front['waves'][1]['submitted_ns']>=front['waves'][0]['finished_ns']
        used=dict(graphs=0,transfers=0,nodes=0);contributions=business=0;graphs=[]
        actual={t['transfer_id']:t for t in report['transfers']};local={t['transfer_id']:t for t in report['local_deliveries']}
        for wave in front['waves']:
            plan=read(path/f"graphs/{wave['graph_id']}/plan.json");usage=actual_usage(plan)
            assert usage==plan['resource_usage'] and usage['transfers']>4096
            used=check_run(used,usage,budget);graphs.append(usage)
            events={(e['rank_id'],e['node_id']):e for e in report['node_execution'] if e['graph_id']==wave['graph_id']}
            assert wave['finished_ns']==max(e['end_ns'] for e in events.values())
            for graph in plan['rank_graphs']:
                for node in graph['nodes']:
                    event=events[graph['rank_id'],node['node_id']]
                    assert all(event['start_ns']>=events[graph['rank_id'],dep]['end_ns'] for dep in node['deps'])
            # Independent protocol count: 12 non-self status stores,
            # two remote contributions per layer, ceil(1068/4)+ceil(2048/4)+1 per contribution.
            assert usage['transfers']==3*(12+2*(267+512+1))
            for child in plan['layers']:
                assert len(child['contributions'])==2;contributions+=2
            for t in plan['transfers']:
                if 'ub_task_id' not in t:
                    x=local[t['transfer_id']];assert x['send_callbacks']==x['recv_callbacks']==1
                else:
                    x=actual[t['transfer_id']];business+=t['business_payload_bytes']
                    assert x['bytes']==x['committed_bytes']==t['payload_bytes'] and x['submitted_tasks']==x['send_callbacks']==x['receive_callbacks']==1
                    assert x['data_ready_ns']<x['transaction_done_ns']==x['send_done_ns']
                    assert events[t['src_rank'],t['send_node_id']]['end_ns']==x['send_done_ns']
                    assert events[t['dst_rank'],t['recv_node_id']]['end_ns']==max(x['data_ready_ns'],x['receive_start_ns'])
        assert used==report['graph_resources']['used'] and contributions==12 and business==12*3072
        audits=[]
        for s in (8,9):
            d=path/f'execution/ports/{s}-4';native=read(d/'observation-audit.json')
            if streamed:
                audit=read(d/'resource-audit.json');audits.append(audit)
                assert audit['logical_window_rows']==6001 and audit['resident_window_rows']<audit['logical_window_rows']
                assert audit['retained_packet_event_records']==audit['retained_queue_records']==audit['retained_packet_range_records']==0
            assert native['all_successful_start_packets']>0
        cases[label]=dict(graphs=graphs,finished_ns=front['finished_ns'],writes=len(actual),business_bytes=business,
            artifact_bytes=size(path),resource_audits=audits)
        return path

    legacy=service('legacy',streamed=False)
    streamed=service('streamed')
    off=service('source-off',provenance=False)
    assert read(legacy/'execution/result.json')==read(streamed/'execution/result.json')==read(off/'execution/result.json')
    for s in (8,9):
        dirs=[p/f'execution/ports/{s}-4' for p in (legacy,streamed,off)]
        assert len({read(d/'observation-audit.json')['wire_and_time_fnv1a64'] for d in dirs})==1
        for name in ('packet-events.jsonl','egress-queue-audit.jsonl'):
            assert dirs[0].joinpath(name).read_bytes()==dirs[1].joinpath(name).read_bytes()==dirs[2].joinpath(name).read_bytes()
        assert dirs[0].joinpath('truth/packet_ranges.jsonl').read_bytes()==dirs[1].joinpath('truth/packet_ranges.jsonl').read_bytes()

    def export(label,raw,obs,expect=None):
        op=save(out/f'{label}.json',obs)
        proc=invoke(label,[sys.executable,'-m','moe_ub_cosim.streaming_export','--raw',raw,'--observation',op,
            '--max-simulation-ns',max_ns,'--output-dir',out/label],1 if expect else 0)
        if expect:
            assert expect in proc.stderr,(label,proc.stderr);failures[label]=expect;return
        result=read(out/label/'metrics/validation.json');assert result['passed'] and result['zero_windows']>0
        cases[label]=dict(artifact_bytes=size(out/label),windows=result['port_windows'],zero_windows=result['zero_windows'])
        return out/label
    old=export_records(legacy,observation,ranks,max_ns);oldout=out/'legacy-export';oldout.mkdir();write_export(oldout,old)
    obs=read(out/'streamed.observation.json');new=export('streamed-export',streamed,obs)
    for name in ('endpoint_ops.jsonl','port_windows.csv'):
        assert (oldout/'observed'/name).read_bytes()==(new/'observed'/name).read_bytes()
    assert (oldout/'truth/attribution_windows.csv').read_bytes()==(new/'truth/attribution_windows.csv').read_bytes()
    assert list(iter_json_lines(new/'metrics/byte-classification.jsonl'))==old['validation']['byte_classification']
    extended=deepcopy(obs)
    for switch in extended['switches']:switch['period_ns']=1000
    expanded=export('extended-export',streamed,extended)
    assert cases['extended-export']['windows']==12002
    public=out/'public-only';shutil.copytree(expanded/'observed',public)
    assert validate_observed(public)['zero_windows']==cases['extended-export']['zero_windows']
    off_obs=read(out/'source-off.observation.json');offout=export('off-export',off,off_obs)
    for name in ('endpoint_ops.jsonl','port_windows.csv','public_config.json'):
        assert (new/'observed'/name).read_bytes()==(offout/'observed'/name).read_bytes()

    low=deepcopy(base);low['execution']['resource_budget']['max_graph_transfers']=100
    service('reject-graph',c=low,expect='Graph resource budget exceeded')
    low=deepcopy(base);low['execution']['resource_budget']['max_run_graphs']=1
    failed=service('reject-run',c=low,expect='Run resource budget exceeded')
    assert not (failed/'graphs/g1').exists()
    low=deepcopy(base);low['execution']['resource_budget']['max_plan_bytes']=100
    service('reject-plan-bytes',c=low,expect='max_plan_bytes')
    for field,error in [('max_event_bytes_per_port','Observation event byte budget exceeded'),
                        ('max_ledger_packets','Observation payload ledger packet budget exceeded'),
                        ('max_registered_transfers','Observation registered transfer budget exceeded')]:
        b=deepcopy(obudget);b[field]=1
        service('reject-'+field,b=b,expect=error)
    # Export budget is independent of raw capture; fail explicitly with partial files, no PASS record.
    tiny=deepcopy(obudget);tiny['max_export_bytes']=100
    rawtiny=service('small-export-budget',b=tiny)
    export('reject-export',rawtiny,read(out/'small-export-budget.observation.json'),'export byte budget exceeded')

    # Direct C++ checks bypass frontend guards. First graph must fail before any UB activation.
    commits=[r for r in iter_json_lines(streamed/'protocol.jsonl') if r['type']=='COMMIT']
    for label,field,value,error in [('backend-graph','max_graph_transfers',100,'graph budget exceeded'),
                                    ('backend-nodes','max_graph_nodes',100,'graph budget exceeded'),
                                    ('backend-bytes','max_plan_bytes',100,'max_plan_bytes'),
                                    ('backend-run','max_run_graphs',1,'run budget exceeded')]:
        target=out/label;target.mkdir();shutil.copytree(streamed/'bootstrap',target/'bootstrap')
        shutil.copytree(streamed/'graphs',target/'graphs');deployment=read(streamed/'deployment.json')
        b=deepcopy(budget);b[field]=value
        def budgets(obj):
            if isinstance(obj,dict):
                if 'resource_budget' in obj:obj['resource_budget']=deepcopy(b)
                for v in obj.values():budgets(v)
            elif isinstance(obj,list):
                for v in obj:budgets(v)
        for p in (target/'graphs').glob('*/plan.json'):
            plan=read(p);budgets(plan);save(p,plan);save(p.parent/'graph-resources.json',b)
        budgets(deployment);save(target/'deployment.json',deployment)
        proc=invoke(label,[binary,base['run_id'],target,system,network,'-',target/'execution',max_ns],1,
            ''.join(json.dumps(c)+'\n' for c in commits))
        assert error in proc.stderr,(label,proc.stderr)
        failure=read(target/'execution/failure.json')
        if label=='backend-run':
            assert failure['now_ns']==849322
            assert not failure['transfers'] and not failure['local_deliveries']
            assert all(r['graph_id']=='g0' and r['finished'] and r['compute_slots']==r['send_slots']==r['pending_tasks']==0
                       for r in failure['ranks'])
        else:assert failure['now_ns']==0 and not failure['transfers']
        failures[label]=error
    save(out/'summary.json',dict(passed=True,cases=cases,failures=failures,commands='commands.json',
        rss_scope='Fresh resource.getrusage(RUSAGE_CHILDREN) maximum individual process RSS, not simultaneous process-tree sum',
        target_scale_executed=False,hardware_calibrated=False))
    print('PASS: resource budgets, >4096-transfer UB graphs, sparse windows, streamed observations and unchanged execution')


if __name__=='__main__':main()
