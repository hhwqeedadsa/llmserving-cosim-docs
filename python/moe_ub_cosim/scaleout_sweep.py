"""Bounded, explicit case lists over the public native Scale-Out workflow."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import re
import subprocess
import sys

from .scaleout_rdma import ROOT,checked_json,digest,monitored,save
from .scaleout_topology import fields,integer,require


def merge(base, patch):
    """Objects merge recursively; null removes an existing optional key."""
    require(isinstance(patch,dict),'case patches must be objects')
    result=deepcopy(base)
    for key,value in patch.items():
        if value is None:
            require(key in result,'cannot remove absent configuration key: '+key)
            del result[key]
        elif isinstance(value,dict):
            require(isinstance(result.get(key,{}),dict),'object patch cannot replace a scalar: '+key)
            result[key]=merge(result.get(key,{}),value)
        else:result[key]=deepcopy(value)
    return result


def resolve(config, directory):
    fields(config,'schema_version base budget cases','sweep')
    require(config['schema_version']=='moe-scaleout-sweep/1','unknown sweep version')
    fields(config['base'],'topology service protocol','sweep base files')
    base={}
    for kind,name in config['base'].items():
        require(isinstance(name,str) and name,'base paths must be strings')
        base[kind]=checked_json((directory/name).resolve(),1048576)
    budget=config['budget'];fields(budget,'wall_seconds rss_bytes address_bytes artifact_bytes','sweep budget')
    for key,lo,hi in [('wall_seconds',1,600),('rss_bytes',268435456,17179869184),
                      ('address_bytes',536870912,34359738368),('artifact_bytes',16777216,536870912)]:
        integer(budget[key],'sweep '+key,lo,hi)
    require(budget['address_bytes']>=budget['rss_bytes'],'sweep address limit below RSS')
    require(isinstance(config['cases'],list) and 1<=len(config['cases'])<=32,'sweep requires 1..32 explicit cases')
    names=set();resolved=[];groups={}
    from .scaleout_topology import generate
    from .scaleout_service import validate
    from .scaleout_limits import limits
    for case in config['cases']:
        fields(case,'name patches'+''.join(' '+k for k in ('expect','comparison_group') if k in case),'sweep case')
        name=case['name']
        require(isinstance(name,str) and re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',name) and name not in names,
                'case names must be unique safe directory names')
        names.add(name);patches=case['patches']
        require(isinstance(patches,dict) and set(patches)<=set(base),'patches may target topology/service/protocol only')
        values={kind:merge(value,patches.get(kind,{})) for kind,value in base.items()}
        topology,_=generate(values['topology']);validate(values['service'],topology,values['protocol'])
        per_case=limits(values['protocol'])
        require(all(per_case[k]<=budget[k] for k in ('wall_seconds','rss_bytes','address_bytes')),
                'case time/memory limits must fit within aggregate sweep budget')
        expect=case.get('expect','success')
        require(expect in ('success','recovery_retry_exhausted','simulation_time_limit'),'unsupported expected terminal condition')
        group=case.get('comparison_group')
        if group is not None:
            require(isinstance(group,str) and re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',group),'invalid comparison group')
            pair=values['topology'],values['service']
            require(group not in groups or groups[group]==pair,'comparison group changes service or topology input')
            groups[group]=pair
        resolved.append(dict(name=name,expect=expect,comparison_group=group,**values))
    return dict(schema_version='moe-scaleout-resolved-sweep/1',budget=budget,cases=resolved)


def metrics(case, run):
    r=checked_json(run/'result.json',1073741824)
    frontend=checked_json(run/'frontend.json',268435456)
    history=frontend['history'];done=bool(history) and r['passed'] and all(h.get('finished_ns') is not None for h in history)
    finish=max(h['finished_ns'] for h in history) if done else None
    recovery=r.get('recovery',{});states=recovery.get('states',[]);cache=recovery.get('history',{})
    return dict(name=case['name'],expect=case['expect'],comparison_group=case['comparison_group'],
        passed=r['passed'],failure_reason=r.get('failure',{}).get('reason'),
        frontend_complete_ns=finish,network_finish_or_stop_ns=r['finish_ns'],
        network_drain_after_frontend_ns=r['finish_ns']-finish if finish is not None else None,
        submitted_messages=len(r['messages']),completed_messages=sum(f['ack_callbacks']==1 for f in r['messages']),
        submitted_logical_bytes=sum(f['bytes'] for f in r['messages']),
        source_payload_bytes=sum(f['source_data_payload_bytes'] for f in r['messages']),
        timeout_retransmissions=sum(s['timeout_retransmissions'] for s in states),
        rtt_samples=sum(s.get('rtt',{}).get('samples',0) for s in states),
        history_hits=cache.get('hits'),history_misses=cache.get('misses'),history_evictions=cache.get('evictions'),
        result_sha256=digest(run/'result.json'),
        graph_hashes={p.parent.name:digest(p) for p in sorted((run/'graphs').glob('*/wave.json'))})


def worker(output, backend, protoc):
    output=Path(output);spec=checked_json(output/'resolved.json',33554432)
    commands=[];records=[];common_graphs={}
    summary=dict(schema_version='moe-scaleout-sweep-result/1',state='running',matches_expectations=False,cases=records,
                 scope='finite ready-KV inference; synthetic or source-checked routes; no tensor accuracy or hardware calibration')
    def cli(name,args,expect_success=True):
        argv=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,args)]
        with (output/(name+'.stdout')).open('w') as f,(output/(name+'.stderr')).open('w') as e:
            completed=subprocess.run(argv,cwd=ROOT,stdout=f,stderr=e,timeout=spec['budget']['wall_seconds'])
        commands.append(dict(argv=argv,cwd=str(ROOT),exit_code=completed.returncode,expected_success=expect_success))
        save(output/'commands.json',commands)
        return completed.returncode
    try:
        for case in spec['cases']:
            directory=output/case['name'];directory.mkdir()
            for kind in ('service','topology','protocol'):save(directory/(kind+'.json'),case[kind])
            require(cli(case['name']+'-topology',['topology','--config',directory/'topology.json','--output',directory/'fabric'])==0,
                    'topology generation failed: '+case['name'])
            code=cli(case['name'],['rdma-run','--config',directory/'service.json','--topology',directory/'fabric',
                '--protocol',directory/'protocol.json','--backend',backend,'--protoc',protoc,'--output',directory/'run'],case['expect']=='success')
            record=metrics(case,directory/'run');records.append(record);save(output/'summary.json',summary)
            require((code==0)==record['passed'],'CLI exit and actual service result disagree: '+case['name'])
            require(record['passed'] if case['expect']=='success' else
                    not record['passed'] and record['failure_reason']==case['expect'],'unexpected terminal result: '+case['name'])
            require(cli(case['name']+'-diagnose',['rdma-diagnose','--run',directory/'run','--output',directory/'diagnosis.json'])==0,
                    'independent diagnosis failed: '+case['name'])
            group=case['comparison_group']
            if group:
                known=common_graphs.setdefault(group,{})
                for graph,checksum in record['graph_hashes'].items():
                    require(graph not in known or known[graph]==checksum,'common submitted task graph changed: '+case['name'])
                    known[graph]=checksum
        summary.update(state='completed',matches_expectations=True,common_graphs_verified=common_graphs)
        save(output/'summary.json',summary)
        columns=[key for key in records[0] if key!='graph_hashes']
        with (output/'summary.csv').open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader()
            writer.writerows({key:record[key] for key in columns} for record in records)
    except Exception as error:
        summary.update(state='unexpected_failure',error=str(error));save(output/'summary.json',summary)
        raise


def run(config, backend, output, protoc='protoc'):
    config,backend,output=(Path(p).resolve() for p in (config,backend,output))
    spec=resolve(checked_json(config,1048576),config.parent)
    require(backend.is_file(),'missing native service backend')
    require(not output.exists(),'sweep output must be a new directory')
    output.mkdir();(output/'request.json').write_bytes(config.read_bytes());save(output/'resolved.json',spec)
    sources=[config,backend,*sorted((ROOT/'python/moe_ub_cosim').glob('scaleout*.py')),ROOT/'python/moe_ub_cosim/cli.py']
    save(output/'provenance.json',dict(platform_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        hashes={str(p):digest(p) for p in sources},network='single_native_ns3_per_case; no UB; no retries',
        case_clock_scope='Each case is an independent shared network execution; case times are never summed as one inference'))
    cmd=[sys.executable,'-m','moe_ub_cosim.scaleout_sweep','--worker',str(output),str(backend),str(protoc)]
    try:
        monitored(cmd,output/'execution',[output],runtime=spec['budget'])
    except Exception as error:
        summary=checked_json(output/'summary.json',33554432) if (output/'summary.json').exists() else dict(cases=[])
        if summary.get('state')!='unexpected_failure':
            summary.update(state='execution_stopped',matches_expectations=False,error=str(error),
                           resource_evidence='execution/probe.json')
            save(output/'summary.json',summary)
        raise
    summary=checked_json(output/'summary.json',33554432)
    require(summary['state']=='completed' and summary['matches_expectations'],'sweep incomplete; preserve failure evidence')
    return dict(passed=True,output=str(output),cases=len(summary['cases']),
                successful_runs=sum(c['passed'] for c in summary['cases']),
                expected_failures=sum(not c['passed'] for c in summary['cases']),state='bounded_sweep_completed')


if __name__=='__main__':
    require(sys.argv[1:2]==['--worker'] and len(sys.argv)==5,'internal sweep worker requires output/backend/protoc')
    worker(*sys.argv[2:])
