"""Public plan -> original scalar-function queries -> QP identity groups.

No platform import, internal truth, packet execution or latency prediction.
"""
import argparse
from collections import Counter,defaultdict
from pathlib import Path
import subprocess
from experiments.provider_binding.stage import load,save


def queries(stage):
    plan=load(stage/'plan/plan.json');assert plan['schema_version']=='moe-modern-deepep-partial-order/2'
    assert not (stage/'queries.json').exists();rows=[];lines=[]
    for o in plan['operations']:
        if o['kind']=='token_data':
            b=o['binding']
            if o['phase']=='dispatch':sm,warp=b['sm'],b['warp'];phase='d'
            else:
                assert b['source_to_owner_count']==1
                index=b['source_rank_prefix'];per=(b['owner_received_tokens']+3)//4
                global_warp=index//per;sm,warp=global_warp%2,global_warp//2;phase='c'
            q=[phase,sm,warp,0]
        else:q=['d',0,-1,1]  # Fixed source uses context0 for counters/barriers.
        rows.append(dict(operation=o['id'],query=q));lines.append(' '.join(map(str,q)))
    save(stage/'queries.json',rows);(stage/'queries.txt').write_text('\n'.join(lines)+'\n')
    # One terminal execution only; outer host_probe includes this CPU child.
    with (stage/'queries.txt').open() as source,(stage/'driver-output.txt').open('x') as out,(stage/'driver-error.txt').open('x') as err:
        result=subprocess.run([str(stage/'binding-cpu')],stdin=source,stdout=out,stderr=err,timeout=30)
    save(stage/'driver-execution.json',dict(exit_code=result.returncode,queries=len(rows),
        kind='two original scalar functions with CPU type/qualifier substitutes; no CUDA or provider execution'))
    assert result.returncode==0


def grouping(rows):
    groups=defaultdict(list)
    for row in rows:
        if row['path']!='local':groups[tuple(row['qp'])].append(row)
    result=[]
    for key,items in sorted(groups.items()):
        roles=sorted({r['phase']+'.'+r['kind'] for r in items})
        result.append(dict(qp=list(key),operations=[r['operation'] for r in items],roles=roles,
            calls=sorted({r['call'] for r in items}),phases=sorted({r['phase'] for r in items}),
            has_data=any(r['kind']=='token_data' for r in items),has_control=any(r['kind']!='token_data' for r in items)))
    return result


def analyze(stage):
    cfg=load(stage/'inputs/input.json');plan=load(stage/'plan/plan.json');ops=plan['operations']
    queries=load(stage/'queries.json');numbers=[list(map(int,line.split())) for line in (stage/'driver-output.txt').read_text().splitlines()]
    assert len(ops)==len(queries)==len(numbers);rows=[]
    for i,(o,q,n) in enumerate(zip(ops,queries,numbers)):
        assert q['operation']==o['id'] and len(n)==6 and n[0]==i
        _,context,conn,local,backend,sharing=n;assert backend==10+conn
        row=dict(operation=o['id'],call=o['call'],phase=o['phase'],kind=o['kind'],src=o['src'],dst=o['dst'],
            path=o['path'],operand_bytes=o['operand_bytes'],context_index=context,connection=conn,
            local_context=local,local_device=cfg['execution']['local_devices'][o['src']][conn],sharing=sharing,
            qp=None if o['path']=='local' else ['shared_devcomm',o['src'],conn,local,o['dst']])
        rows.append(row)
    groups=grouping(rows);remote=[g for g in groups if g['qp'][1]!=g['qp'][4]]
    collapsed=defaultdict(set)
    for g in remote:collapsed[g['qp'][1],g['qp'][4]].add(tuple(g['qp']))
    roles={}
    for role in sorted({r['phase']+'.'+r['kind'] for r in rows}):
        subset=[r for r in rows if r['phase']+'.'+r['kind']==role]
        roles[role]=dict(operations=len(subset),by_path=dict(sorted(Counter(r['path'] for r in subset).items())),
            active_qps=len({tuple(r['qp']) for r in subset if r['qp']}),
            active_contexts=sorted({r['context_index'] for r in subset if r['qp']}))
    return dict(schema_version='provider-binding-audit/1',rows=rows,groups=groups,roles=roles,
        summary=dict(operations=len(rows),provider_operations=sum(r['qp'] is not None for r in rows),
            local_bypasses=sum(r['qp'] is None for r in rows),active_provider_qps=len(groups),active_remote_qps=len(remote),
            active_self_primary_qps=len(groups)-len(remote),
            data_control_shared_qps=sum(g['has_data'] and g['has_control'] for g in groups),
            dispatch_combine_shared_qps=sum(len(g['phases'])>1 for g in groups),
            multiple_call_shared_qps=sum(len(g['calls'])>1 for g in groups),
            per_call_active_qps=sum(len(g['calls']) for g in groups),
            allocated_contexts=6,allocated_main_qps=6*4*4,allocated_self_responder_qps=6*4,
            allocated_qp_objects=6*4*5,independent_four_devcomms_allocated_objects=4*6*4*5,
            legacy_direction_qps=len(collapsed),directions_with_multiple_modern_qps=sum(len(v)>1 for v in collapsed.values())),
        scope='fixed declared launch and same Context lifetime across four calls; identities and operand bytes only',
        network_executions=0,hardware_idle_state='not established by host creation or scalar indexing code',
        excluded_claims=['modern performance','CNP discard/reset safety','NIC idle controller calibration','A-class novelty'])


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['queries','analyze']);p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.action=='queries':queries(a.stage)
    else:
        assert a.output and not a.output.exists();save(a.output,analyze(a.stage))


if __name__=='__main__':main()
