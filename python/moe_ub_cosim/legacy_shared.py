"""Complete independent legacy groups on one shared native network."""
from copy import deepcopy
import os
from pathlib import Path
import shutil

from .deepep_indexed import check as check_indexed
from .legacy_native import lower, minimum_reference_events, validate_native_inputs
from .scaleout_rdma import checked_json, digest, monitored, save, validate_probe
from .deepep_plan import fields, integer, require


def aggregate(groups, transport):
    """Keep local rank/expert namespaces, allocating only shared wire ranges."""
    require(isinstance(groups,list) and 1<=len(groups)<=2,'shared execution requires one or two groups')
    used=set();ids=set();streams=work=operations=events=0;mapping=[]
    for group in groups:
        fields(group,'id execution','shared group')
        identity=group['id']
        require(isinstance(identity,str) and 1<=len(identity)<=64 and identity not in ids,'invalid/duplicate shared group id')
        ids.add(identity);execution=group['execution'];gpus=execution['rank_to_gpu']
        require(len(set(gpus))==len(gpus) and not used.intersection(gpus),'shared groups overlap physical endpoints')
        used.update(gpus)
        count=execution['limits']['streams']
        mapping.append(dict(id=identity,stream_offset=streams,streams=count,rank_to_gpu=gpus))
        streams+=count;work+=execution['limits']['work']
        operations+=len(execution['operations']);events+=len(execution['events'])
    require(0<streams<=32768 and 20000+streams<=65536 and 0<work<=65536 and
            operations<=65536 and events<=280000,'shared aggregate lifetime budget exceeded')
    return dict(schema_version='moe-legacy-shared-execution/1',groups=groups,**deepcopy(transport)),dict(
        groups=mapping,streams=streams,work=work,operations=operations,events=events)


def run(config,topology,protocol,transport,backend,output, *, validate_only=False):
    require(type(validate_only) is bool,'validate_only must be boolean')
    config,topology,protocol,transport,backend,output=map(lambda p:Path(p).resolve(),
        (config,topology,protocol,transport,backend,output))
    require(not output.exists() and output.parent.is_dir(),'shared output must be a new directory with existing parent')
    require(backend.is_file() and os.access(backend,os.X_OK),'pass shared-capable legacy native executable')
    require(shutil.disk_usage(output.parent).free>=8*2**30+512*2**20,'shared output needs 8GiB reserve')
    source=checked_json(config,65536);fields(source,'schema_version groups','shared input')
    require(source['schema_version']=='moe-legacy-shared-input/1','unsupported shared input schema')
    require(isinstance(source['groups'],list) and 1<=len(source['groups'])<=2,'shared input requires one or two groups')
    t=checked_json(topology/'topology.json',8*2**20);p=checked_json(protocol,65536);recovery=checked_json(transport,65536)
    groups=[];checks=[];references={str(path):digest(path) for path in (config,topology/'topology.json',protocol,transport,backend)}
    local_protocol=deepcopy(p);local_protocol.pop('faults',None)
    output.mkdir()
    for index,group in enumerate(source['groups']):
        fields(group,'id plan binding','shared source group')
        require(isinstance(group['plan'],str) and group['plan'] and isinstance(group['binding'],str) and group['binding'],
                'shared plan/binding paths must be nonempty strings')
        plan=(config.parent/group['plan']).resolve();binding=(config.parent/group['binding']).resolve()
        b=checked_json(binding,65536)
        budget=validate_native_inputs(t,p,b,recovery,reference_event_cap=200000,shared_resources=True)
        checked=check_indexed(plan,output/f'group-{index}-plan-check.json')
        require(checked['passed'],'shared source plan check failed')
        execution=lower(plan,b,t,local_protocol);execution.update(deepcopy(recovery))
        groups.append(dict(id=group['id'],execution=execution));checks.append(checked)
        references.update({str(path):digest(path) for path in (binding,plan/'manifest.json')})
    execution,summary=aggregate(groups,recovery)
    mandatory=sum(minimum_reference_events(group['execution'],p['payload_bytes']) for group in groups)
    require(recovery['dcqcn_reference']['max_events']>=mandatory,
            f'shared reference events below aggregate mandatory source sends={mandatory}; no network started')
    first=groups[0]['execution']['rank_to_gpu'][:2]
    validate_probe(t,p,[dict(id=0,src=first[0],dst=first[1],bytes=1,plane=0,ready_ns=0)],
                   match_faults=False,legacy_messages=False)
    for rule in p.get('faults',[]):
        integer(rule['message_id'],0,summary['streams']-1,'shared fault stream')
        owner=next(row for row in summary['groups'] if row['stream_offset']<=rule['message_id']<row['stream_offset']+row['streams'])
        require(rule['node'] in owner['rank_to_gpu'],'shared fault outside owning group endpoints')
    save(output/'execution.json',execution)
    command=[backend,topology/'topology.json',protocol,output/'execution.json',output/'result.json']
    receipt=dict(schema_version='moe-legacy-shared-run/1',**summary,hashes=references,
        execution_sha256=digest(output/'execution.json'),minimum_reference_source_events=mandatory,
        endpoint_count=sum(n['kind']=='gpu' for n in t['nodes']),
        validation_only=validate_only,physical_gpu_executed=False)
    receipt['planned_native_command' if validate_only else 'command']=list(map(str,command))
    save(output/'inputs.json',receipt)
    if validate_only:
        validation=dict(receipt,passed=True,native_admission_executed=False,native_network_executed=False,
            scope='Python and full source-plan checks only; no native admission, packets, completion or calibration')
        save(output/'validation.json',validation);return validation
    monitored(command,output/'execution-monitor',[output],runtime=budget)
    result=checked_json(output/'result.json',budget['result_json_bytes'])
    require(result.get('passed') and result['legacy_shared']['finished'],'incomplete shared legacy execution')
    return dict(passed=True,**summary,output=str(output),native_network_executed=True,hardware_calibrated=False)
