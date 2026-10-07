"""Freeze interventions from a normal reference, then execute without retuning."""
import math
from pathlib import Path
import shutil

from moe_ub_cosim.research_storage import stored_rows

from .common import digest, read, require, save
from .workflow import host, platform


def freeze_small(baseline, output):
    baseline,output = Path(baseline).resolve(),Path(output).resolve()
    require(not output.exists(), 'Frozen case directory already exists')
    run = baseline/'r000'
    report = read(run/'raw/execution/result.json')
    require(report['normal_exit'], 'Need successful normal reference')
    operations = list(stored_rows(run/'truth','operation_registry.jsonl'))
    selected = [r for r in operations if r['layer_id']==2]
    require({r['kind'] for r in selected}=={'dispatch','combine'}, 'Missing normal target layer')
    start = min(e['start_global_ns'] for o in selected for e in o['endpoint_records'])
    end = max(e['end_global_ns'] for o in selected for e in o['endpoint_records'])
    span = end-start; period = 100
    interval = dict(node_id=8,port_id=4,start_ns=start//period*period,
                    end_ns=math.ceil(end/period)*period,rate_bps=112_000_000_000)
    cases = [dict(case_id='r000',endpoint_prepare=[],port_service=[],purpose='normal')]
    for case_id,fraction in [('r001',0.1),('r002',0.5)]:
        cases.append(dict(case_id=case_id,purpose='endpoint_preparation',
            endpoint_prepare=[dict(rank_id=3,layer_id=2,dispatch_ns_per_local_token=math.ceil(span*fraction/4),
                                   combine_ns_per_expert_input=0)],port_service=[]))
    for case_id,rate in [('r003',112_000_000_000),('r004',56_000_000_000)]:
        cases.append(dict(case_id=case_id,purpose='physical_egress_service',endpoint_prepare=[],
                          port_service=[dict(interval,rate_bps=rate)]))
    quiet = math.ceil(report['service_finished_ns']/period)*period+1000
    cases.append(dict(case_id='r005',purpose='quiescent_egress_negative_control',endpoint_prepare=[],
        port_service=[dict(interval,start_ns=quiet,end_ns=quiet+1000,rate_bps=56_000_000_000)]))
    output.mkdir(parents=True)
    for case in cases:
        case['schema_version']='mapping-value-case/1'
        case['normal_manifest_sha256']=digest(run/'manifest.json')
        save(output/(case['case_id']+'.json'),case)
    save(output/'freeze.json',dict(schema_version='mapping-value-freeze/1',normal_run=str(run),
        normal_manifest_sha256=digest(run/'manifest.json'),selected_layer=2,
        normal_layer_operation_span_ns=span,normal_interval_ns=[start,end],
        endpoint_extra_fractions=[0.1,0.5],endpoint_local_tokens=4,
        alignment_period_ns=period,cases={c['case_id']:digest(output/(c['case_id']+'.json')) for c in cases},
        rule='Fixed from normal reference before treatments; no automatic strength increase or retry'))
    return dict(passed=True,output=str(output),cases=[c['case_id'] for c in cases],normal_layer_span_ns=span)


def run_case(baseline, case_path, output, backend):
    baseline,output,backend = map(lambda p:Path(p).resolve(),(baseline,output,backend))
    case = read(case_path)
    require(case['schema_version']=='mapping-value-case/1', 'Unsupported case')
    require(case['normal_manifest_sha256']==digest(baseline/'r000/manifest.json'), 'Normal reference changed')
    require(not output.exists(), 'Case output already exists')
    output.mkdir(parents=True); save(output/'case.json',case)
    source = read(baseline/'run.json')
    if case['endpoint_prepare']:
        layers = read(baseline/'resolved/layer.json')
        for change in case['endpoint_prepare']:
            matches = [l for g in layers['groups'] for l in g['layers'] if l['layer_id']==change['layer_id']]
            require(len(matches)==1,'Unknown endpoint target layer')
            ranks = matches[0]['endpoint_prepare']['ranks']
            matches = [r for r in ranks if r['rank_id']==change['rank_id']]
            require(len(matches)==1,'Unknown endpoint target rank')
            matches[0].update({k:change[k] for k in ('dispatch_ns_per_local_token','combine_ns_per_expert_input')})
        save(output/'layers.json',layers)
        source['inputs']['layer']=str(output/'layers.json')
        config=source; action='run'; extra=[]
    else:
        config=dict(schema_version='moe-ub-replay/2',host=host(),backend=str(backend),
            backend_timeout_seconds=1700,export_policy=source['export_policy'],
            max_simulation_ns=source['max_simulation_ns'])
        action='replay'; extra=['--slice',baseline/'slice']
    config['backend']=str(backend)
    if case['port_service']:
        shutil.copytree(baseline/'resolved/network',output/'network')
        network=read(output/'network/backend.json')
        network['schema_version']='moe-ub-backend-config/3'
        network['port_service']=dict(schema_version='moe-ub-port-service/1',
            execution_end_ns=config['max_simulation_ns'],intervals=case['port_service'])
        save(output/'network/backend.json',network)
        if action=='run':
            config['inputs']['network']=str(output/'network')
        else:
            config['network']=str(output/'network')
    save(output/'execute.json',config)
    platform([action,'--config',output/'execute.json','--output',output/'run',*extra],output,'01-'+action)
    platform(['validate','--run',output/'run'],output,'02-validate')
    return dict(passed=True,output=str(output),run='run',case_sha256=digest(case_path))
