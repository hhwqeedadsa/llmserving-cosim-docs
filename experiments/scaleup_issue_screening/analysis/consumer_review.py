"""Bounded researcher review of all last Combine consumers and completion conditions.

Reads verified archived records without restoring a run or simulating. Incoming
data is one condition, not a substitute for status, local work or scheduling.
"""
import argparse
from collections import defaultdict
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

from .workflow import digest, host, read, save


PREFIX = 'run/raw/execution/records/'


class Archive:
    def __init__(self, path):
        self.path = Path(path)
        self.index = read(str(path)+'.json')
        if not self.index['verified'] or digest(path) != self.index['archive_sha256']:
            raise ValueError('Archive does not match its verified inventory')
        self.used = {}

    def members(self, names):
        missing = set(names)
        if not missing <= self.index['entries'].keys():
            raise ValueError('Missing required archived members')
        with tarfile.open(self.path, 'r|gz') as stream:
            for member in stream:
                if member.name not in missing:
                    continue
                if not member.isfile() or member.size > 2*2**30:
                    raise ValueError('Selected member exceeds the plan budget or is not a file')
                data = stream.extractfile(member).read()
                identity = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                if identity != self.index['entries'][member.name]:
                    raise ValueError('Archived member identity mismatch')
                self.used[member.name] = identity
                missing.remove(member.name)
                yield member.name, data
                del data
        if missing:
            raise ValueError('Archive omitted selected members')

    def rows(self, field):
        names = [n for n in self.index['entries'] if n.startswith(PREFIX+field+'-') and n.endswith('.jsonl.gz')]
        if not names:
            raise ValueError('Missing archived record family: '+field)
        for _, data in self.members(names):
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                for line in stream:
                    if len(line)>2**20:
                        raise ValueError('Oversized execution record')
                    yield json.loads(line)


def condition_class(node, transfers):
    if node['kind']=='RECV':
        return 'incoming_'+transfers[node['transfer_id']]['message_role']
    name=node['name']
    if name=='combine-core-sends-complete':return 'local_core_sends'
    if name=='combine-token-reduction':return 'token_reduction'
    if name=='combine-entry':return 'combine_entry'
    if node.get('event') in ('data_ready','local_read_complete'):return 'local_send_'+node['event']
    return 'local_dependency'


def completion_conditions(plan, times, transfers, actual, finish_id, layer, kind='combine'):
    """All finish inputs/reduction gates plus the DAG of all latest ties.

    Exclusive-resource holders are reconstructed from actual COMP intervals and
    the resource key configured by the backend. Uncovered gaps stay explicit.
    """
    def brief(nid):
        n,r=plan[nid],times[nid]
        return dict(node_id=nid,name=n['name'],kind=n['kind'],condition=condition_class(n,transfers),
                    start_ns=r['start_ns'],end_ns=r['end_ns'],resource_id=n.get('resource_id'),
                    transfer_id=n.get('transfer_id'))

    def detail(nid):
        n,r=plan[nid],times[nid]
        deps=[brief(d) for d in n['deps']]
        ready=max((d['end_ns'] for d in deps),default=0)
        if not ready<=r['start_ns']<=r['end_ns']:
            raise ValueError('Invalid dependency/start ordering')
        ties=[d['node_id'] for d in deps if d['end_ns']==ready]
        row=dict(**brief(nid),dependency_ready_ns=ready,ready_to_start_ns=r['start_ns']-ready,
                 dependencies=deps,latest_dependency_ties=ties)
        resource=n.get('resource_id')
        owners=[]
        if ready<r['start_ns'] and resource:
            for other,p in plan.items():
                t=times[other]
                if other!=nid and p['kind']=='COMP' and p.get('resource_id')==resource:
                    lo,hi=max(ready,t['start_ns']),min(r['start_ns'],t['end_ns'])
                    if lo<hi:owners.append(dict(**brief(other),overlap_start_ns=lo,overlap_end_ns=hi))
        owners.sort(key=lambda p:(p['overlap_start_ns'],p['node_id']))
        covered=0;until=ready
        for o in owners:
            if o['overlap_start_ns']<until:raise ValueError('Exclusive resource owners overlap')
            covered+=o['overlap_end_ns']-o['overlap_start_ns'];until=o['overlap_end_ns']
        row['resource_owners']=owners
        row['unexplained_scheduling_ns']=r['start_ns']-ready-covered
        tid=n.get('transfer_id')
        field=('data_ready_ns' if n['kind']=='RECV' or n.get('event')=='data_ready' else
               'local_read_complete_ns' if n.get('event')=='local_read_complete' else None)
        if field and tid is not None:
            t,a=transfers[tid],actual[tid]
            local='ub_task_id' not in t
            at=a['ready_ns'] if local else (a[field] if field in a else a['local_facts'][field])
            if r['end_ns']!=max(r['start_ns'],at):
                raise ValueError('Recorded callback does not explain event completion')
            row['delivery_condition']=dict(field=field,time_ns=at,
                pending_after_start=at>r['start_ns'],coincident_with_start=at==r['start_ns'],
                submitted_ns=a['ready_ns'] if local else a['send_start_ns'],
                protocol_retired_ns=a['ready_ns'] if local else a['transaction_done_ns'],
                identity={k:t[k] for k in ('transfer_id','operation_id','src_rank','dst_rank',
                    'message_role','message_id','chunk_index','payload_bytes','sources') if k in t})
        return row

    finish=detail(finish_id)
    if kind not in ('dispatch','combine'):raise ValueError('Unknown operation kind')
    reductions=[detail(i) for i,n in plan.items() if kind=='combine' and n.get('layer_id')==layer and n['name']=='combine-token-reduction']
    sends=[detail(i) for i,n in plan.items() if kind=='combine' and n.get('layer_id')==layer and n['name']=='combine-core-sends-complete']
    pending=[finish_id];seen={}
    while pending:
        nid=pending.pop()
        if nid in seen:continue
        if len(seen)>=10000:raise ValueError('Completion witness exceeds 10000 nodes')
        row=detail(nid);seen[nid]=row
        if plan[nid].get('layer_id')!=layer or plan[nid]['name']==kind+'-entry':
            row['boundary']='layer entry; prior execution retained in source records'
        elif row.get('delivery_condition',{}).get('pending_after_start'):
            row['boundary']='actual transfer callback; sender/resource/network interval not further split'
        else:
            pending.extend(row['latest_dependency_ties'])
        # Dependency readiness and an occupied resource are separate gates.
        # Keep the actual occupying nodes too, without labeling uncovered time.
        pending.extend(o['node_id'] for o in row['resource_owners'])
    result=dict(finish=finish,all_token_reductions=reductions,all_core_send_barriers=sends,
                latest_completion_dag=list(seen.values()),
                scope='All finish gates, all token reduction inputs, all tied latest branches; callback and prior-layer boundaries explicit')
    if kind=='dispatch':
        result.update(all_data_status_gates=[detail(i) for i,n in plan.items() if n.get('layer_id')==layer and
            n['name'] in ('dispatch-counts-and-data-ready','dispatch-cross-core-data-barrier')],
            all_reorder_nodes=[detail(i) for i,n in plan.items() if n.get('layer_id')==layer and n['name']=='dispatch-reorder'],
            scope='All Dispatch finish, data/status and local data barriers, all reorder nodes and tied latest branches; callback boundary explicit')
    return result


def review(plan_archive, archive, output):
    output=Path(output)
    if output.exists():raise ValueError('Consumer review output must be new')
    source=Archive(archive);basis=source if Path(plan_archive)==Path(archive) else Archive(plan_archive)
    endpoints={(r['rank_id'],r['local_record_id']):r for r in source.rows('endpoint_ops')}
    operations=list(source.rows('operation_registry'));selected=[]
    for op in operations:
        if op['kind']!='combine' or op['layer_id'] not in (7,8):continue
        members={m['rank_id']:endpoints[m['rank_id'],m['local_record_id']] for m in op['endpoint_records']}
        end=max(v['end_local_ns'] for v in members.values())
        ranks={r for r,v in members.items() if v['end_local_ns']==end}
        ranks.add({7:27,8:58}[op['layer_id']])
        for rank in sorted(ranks):
            selected.append(dict(layer_id=op['layer_id'],rank_id=rank,operation_id=op['operation_id'],
                is_actual_last=members[rank]['end_local_ns']==end,combine_end_ns=members[rank]['end_local_ns'],
                all_last_consumers=sorted(r for r,v in members.items() if v['end_local_ns']==end)))
    if not selected:raise ValueError('Missing selected Combine operations')
    ranks={t['rank_id'] for t in selected};plans={};models={}
    names=[n for n in basis.index['entries'] if n.startswith('run/raw/graphs/') and n.endswith('/plan.json')]
    for name,data in basis.members(names):
        p=json.loads(data);gid=p['graph_id']
        if p['communication_template']!='cann_a3_v850_store_resources_v1':raise ValueError('Expected A3 reference plan')
        # Bind old frozen graph to this execution; no inferred transfer identity.
        field=PREFIX+'planned_transfers-'+gid+'.jsonl.gz'
        if basis.index['entries'][field]!=source.index['entries'][field]:
            raise ValueError('Execution transfer plan differs from selected original graph')
        for graph in p['rank_graphs']:
            if graph['rank_id'] in ranks:
                key=gid,graph['rank_id'];plans[key]={n['node_id']:n for n in graph['nodes']}
                models[key]=graph.get('model_stages',[])
        del p,data
    if len(plans)!=len(ranks):raise ValueError('Expected one bounded graph covering selected ranks')
    times=defaultdict(dict)
    for r in source.rows('node_execution'):
        key=r['graph_id'],r['rank_id']
        if key in plans:times[key][r['node_id']]=r
    for key,plan in plans.items():
        if set(plan)!=set(times[key]):raise ValueError('Node execution coverage mismatch')
    transfers={t['transfer_id']:t for t in source.rows('planned_transfers') if {t['src_rank'],t['dst_rank']}&ranks}
    actual={t['transfer_id']:t for field in ('transfers','local_deliveries') for t in source.rows(field) if t['transfer_id'] in transfers}
    if set(actual)!=set(transfers):raise ValueError('Transfer execution coverage mismatch')
    for target in selected:
        key=next(k for k in plans if k[1]==target['rank_id']);p=plans[key];ts=times[key]
        finishes=[i for i,n in p.items() if n.get('layer_id')==target['layer_id'] and n['name']=='combine-operator-complete']
        if len(finishes)!=1 or ts[finishes[0]]['end_ns']!=target['combine_end_ns']:
            raise ValueError('Endpoint and actual completion node disagree')
        target.update(graph_id=key[0],completion=completion_conditions(p,ts,transfers,actual,finishes[0],target['layer_id']))
        target['following_compute']=[dict(stage=s['stage'],layer_id=s['layer_id'],**ts[s['node_id']])
            for s in models[key] if s['node_id'] is not None and
            (s['layer_id'],s['stage']) in [(target['layer_id'],'shared_routed_add'),(target['layer_id'],'ffn_residual'),
                (target['layer_id']+1,'attention_norm'),(target['layer_id']+1,'router')]]
    save(output,dict(schema_version='issue-screening-consumer-completion/1',passed=True,
        archive=str(source.path),archive_sha256=source.index['archive_sha256'],
        original_manifest=source.index['entries']['run/manifest.json'],
        plan_archive=str(basis.path),plan_archive_sha256=basis.index['archive_sha256'],
        source_members=source.used,plan_members=basis.used,targets=selected,
        program_sha256=digest(__file__),simulation_executed=False,
        caveats=['The previous latest_incoming_data field retains its data-only meaning.',
                 'All latest ties are retained; callback boundaries do not assign switch or endpoint queue causes.',
                 'Resource holders use actual intervals on the declared exclusive resource, not time proximity alone.']))
    print([(t['layer_id'],t['rank_id'],t['combine_end_ns']) for t in selected])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('plan-archive','archive','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:review(a.plan_archive,a.archive,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],str(a.output)+'-resources',**host())
        if r['status']!='completed':raise SystemExit('Consumer review failed: '+r['status'])


if __name__=='__main__':main()
