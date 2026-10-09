"""Selected internal execution facts; never an observed-only analysis input."""
from pathlib import Path

from .plan import integer,require
from .user_io import fields,inside,read,save,inventory
from .record_storage import iter_result
from .research_storage import ResearchWriter
from .plan_io import file_sha256


LIMITS={'max_graphs','max_nodes','max_transfers','max_plan_bytes','max_compressed_bytes','max_record_bytes'}


def policy(value):
    fields(value,{'schema_version','host','selection','limits'},label='local facts')
    require(value['schema_version']=='moe-ub-local-facts/1','Unsupported internal facts policy')
    fields(value['selection'],{'graph_ids','rank_ids','layer_ids'},label='local facts selection')
    for key,values in value['selection'].items():
        require(isinstance(values,list) and values and len(values)==len(set(values)),f'Explicit nonempty unique {key} required')
        for v in values:
            if key=='graph_ids':require(isinstance(v,str) and v,'Invalid graph identity')
            else:integer(v,key,0)
    fields(value['limits'],LIMITS,label='local facts limits')
    for key,v in value['limits'].items():integer(v,key,1)
    require(value['limits']['max_record_bytes']<=64*2**20,'Fact record size exceeds reader bound')
    require(len(value['selection']['graph_ids'])<=value['limits']['max_graphs'],'Fact graph coverage budget exceeded')
    return value


def export(run,config,output):
    """Read sealed inputs under a declared plan-size and host-memory bound."""
    from .user_workflow import validate
    run=Path(run).resolve();output=Path(output);config=policy(config)
    validate(run,allow_partial_observation=True)
    manifest=read(run/'manifest.json')
    require(manifest['action']=='run','Internal facts require the original configured run with sealed semantic plans')
    selection=config['selection'];limits=config['limits'];raw=run/'raw';execution=raw/'execution'
    report=read(execution/'result.json');selected={};needed=set();transfers={};seen=set()
    protocol=[__import__('json').loads(row) for row in (raw/'protocol.jsonl').read_text().splitlines()]
    submissions=[s for c in protocol if c['type']=='COMMIT' for s in c['submissions']]
    coverage={};source_hashes={}
    for submission in submissions:
        gid=submission['graph_id']
        if gid not in selection['graph_ids']:continue
        require(gid not in seen,'Repeated selected graph');seen.add(gid)
        path=inside(raw,submission['semantic_manifest'])
        require(path.stat().st_size<=limits['max_plan_bytes'],'Selected graph exceeds max_plan_bytes; no partial facts emitted')
        source_hashes[str(path.relative_to(run))]=file_sha256(path)
        plan=read(path)
        require(plan['communication_template']=='cann_a3_v850_store_resources_v1','Local facts require the A3 resource template')
        require(set(selection['rank_ids'])<=set(plan['rank_ids']) and
                set(selection['layer_ids'])<=set(plan.get('communication_layer_ids',[plan['input'].get('layer_id')])),
                'Requested rank/layer coverage is absent')
        # Keep only requested node metadata and direct predecessor identities;
        # large plans are read one graph at a time, not retained across graphs.
        transfer_layers={}
        for graph in plan['rank_graphs']:
            rank=graph['rank_id']
            for node in graph['nodes']:
                lid=node.get('layer_id',plan['input'].get('layer_id'))
                if node['kind']=='SEND':transfer_layers[node['transfer_id']]=lid
                if rank not in selection['rank_ids'] or lid not in selection['layer_ids']:continue
                require(len(selected)<limits['max_nodes'],'Fact node coverage budget exceeded')
                key=gid,rank,node['node_id'];require(key not in selected,'Repeated fact node')
                selected[key]=node;needed.add(key);needed.update((gid,rank,d) for d in node['deps'])
                require(len(needed)<=2*limits['max_nodes'],'Fact dependency coverage exceeds twice max_nodes')
        for transfer in plan['transfers']:
            lid=transfer_layers[transfer['transfer_id']]
            if lid not in selection['layer_ids'] or not set(selection['rank_ids'])&{transfer['src_rank'],transfer['dst_rank']}:continue
            require(len(transfers)<limits['max_transfers'],'Fact transfer coverage budget exceeded')
            key=gid,transfer['transfer_id'];require(key not in transfers,'Repeated fact transfer')
            transfers[key]=dict(transfer,layer_id=lid)
        coverage[gid]=dict(available_ranks=plan['rank_ids'],available_layers=plan.get('communication_layer_ids',[plan['input'].get('layer_id')]),
            selected_nodes=sum(k[0]==gid for k in selected),selected_transfers=sum(k[0]==gid for k in transfers))
        del plan
    require(seen==set(selection['graph_ids']) and selected,'Missing selected graph or node coverage')
    times={}
    for row in iter_result(execution,'node_execution',report):
        key=row['graph_id'],row['rank_id'],row['node_id']
        if key in needed:
            require(key not in times,'Duplicate node execution');times[key]=row
    require(set(times)==needed,'Missing selected node/dependency execution')
    actual={}
    for field in ('transfers','local_deliveries'):
        for row in iter_result(execution,field,report):
            key=row['graph_id'],row['transfer_id']
            if key in transfers:
                require(key not in actual,'Duplicate selected transfer execution');actual[key]=row
    require(set(actual)==set(transfers),'Missing selected transfer execution')
    writer=ResearchWriter(output/'tables',limits['max_compressed_bytes'],limits['max_record_bytes'])
    try:
        writer.open('truth','nodes.jsonl');writer.open('truth','deliveries.jsonl')
        for key,node in selected.items():
            time=times[key];deps=[dict(node_id=d,end_ns=times[key[0],key[1],d]['end_ns']) for d in node['deps']]
            ready=max((d['end_ns'] for d in deps),default=0)
            require(ready<=time['start_ns']<=time['end_ns'],'Node readiness exceeds actual start')
            row=dict(graph_id=key[0],rank_id=key[1],node_id=key[2],layer_id=node.get('layer_id'),
                kind=node['kind'],name=node['name'],resource_id=node.get('resource_id'),
                dependency_ready_ns=ready,start_ns=time['start_ns'],end_ns=time['end_ns'],dependencies=deps,
                ready_to_start_ns=time['start_ns']-ready,event=node.get('event'),transfer_id=node.get('transfer_id'))
            writer.add('truth','nodes.jsonl',row)
        for key,t in transfers.items():
            result=actual[key];fabric='ub_task_id' in t
            if fabric:
                facts=result.get('local_facts')
                require(facts and facts['schema_version']=='moe-ub-transfer-local-facts/1','Missing actual native category/local completion facts')
                local_read,business,metadata=(facts[k] for k in ('local_read_complete_ns','business_ready_ns','application_metadata_ready_ns'))
                submitted,ready,retired=(result[k] for k in ('send_start_ns','data_ready_ns','transaction_done_ns'))
            else:
                submitted=local_read=ready=retired=result['ready_ns']
                business=ready if t['business_payload_bytes'] else None
                metadata=ready if t['application_metadata_bytes'] else None
            require((business is not None)==bool(t['business_payload_bytes']) and
                (metadata is not None)==bool(t['application_metadata_bytes']),'Category completion coverage differs from bytes')
            require(submitted<=local_read<=retired and ready<=retired and
                all(submitted<=v<=ready for v in (business,metadata) if v is not None),'Invalid local completion ordering')
            row={k:t[k] for k in ('transfer_id','operation_id','src_rank','dst_rank','layer_id','kind','message_role',
                'payload_bytes','business_payload_bytes','application_metadata_bytes','send_node_id','recv_node_id','sources')}
            row.update(graph_id=key[0],delivery='native_store' if fabric else 'same_chip',
                data_owner_rank=t['src_rank'],consumer_rank=t['dst_rank'],
                submitted_ns=submitted,local_read_complete_ns=local_read,business_ready_ns=business,
                application_metadata_ready_ns=metadata,all_payload_ready_ns=ready,protocol_retired_ns=retired)
            writer.add('truth','deliveries.jsonl',row)
        writer.finish(0)
    finally:writer.close()
    # Move sealed tables, without copying; an incomplete worker retains tables/.
    (output/'tables/truth').rename(output/'truth');(output/'tables').rmdir()
    value=dict(schema_version='moe-ub-local-facts-result/1',passed=True,time_domain='global_simulation_ns',
        access_scope='internal_truth_not_observed',selection=selection,coverage=coverage,limits=limits,
        selected_nodes=len(selected),selected_transfers=len(transfers),source_plan_sha256=source_hashes,
        source_manifest_sha256=file_sha256(run/'manifest.json'),simulation_executed=False,
        readiness='declared_dependency_completion; ready_to_start includes resource/scheduler wait, not exclusively network queue',
        local_read_basis='all native STORE requests formed; reference buffer reuse event, not hardware HBM timing',
        artifacts=inventory(output/'truth'))
    save(output/'facts.json',value);return value


def execute(run,config_path,output):
    import sys
    from .user_io import budgeted_worker,host_policy
    path=Path(config_path).resolve();config=policy(read(path))
    return budgeted_worker('moe_ub_cosim.local_facts',[str(Path(run).resolve()),str(path)],output,
        host_policy(config['host'],path.parent),'facts.json')


if __name__=='__main__':
    import sys
    export(Path(sys.argv[1]),read(sys.argv[2]),Path(sys.argv[3]))
