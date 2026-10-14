"""Prepare and screen all declared normal-run outlets with a frozen rule.

Researcher preparation only. No outcome labels, ideal application identities or
simulation are used to select checks. Sparse zeros require explicit coverage.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
import sys

from .consumer_review import Archive
from .organization import resolved_members, zipped_rows
from .workflow import digest, host, read, save


def ports(network, public):
    """Derive plane and physical role from the validated two-tier fabric."""
    shape=network['fabric']['shape']
    dies=shape['chips_per_server']*shape['dies_per_chip']
    devices=shape['servers']*dies
    l2=devices+shape['servers']*shape['planes']
    nodes={n['node_id']:n for n in network['topology']['nodes']}
    neighbors={}
    for link in network['topology']['links']:
        for a,b in (('src','dst'),('dst','src')):
            key=link[a],link[a+'_port']
            if key in neighbors:raise ValueError('Duplicate directed physical outlet')
            neighbors[key]=(link[b],link[b+'_port'],link['rate_bps'])
    result={}
    for c in public['ports']:
        key=c['switch_id'],c['port_id'];n,p=key
        if key not in neighbors or key in result:raise ValueError('Unknown or repeated observed outlet')
        peer,peer_port,rate=neighbors[key]
        if nodes[n]['kind']=='DEVICE':plane=p;role='device_source';group=('source',n)
        elif n<l2:
            plane=(n-devices)%shape['planes']
            if nodes[peer]['kind']=='DEVICE':role='device_bound';group=('destination',peer)
            else:role='inter_switch';group=('l1_up',(n-devices)//shape['planes'],(peer-l2)%shape['l2_per_plane'])
        else:
            plane=(n-l2)//shape['l2_per_plane'];role='inter_switch'
            group=('l2_down',(n-l2)%shape['l2_per_plane'],(peer-devices)//shape['planes'])
        result[key]=dict(switch_id=n,port_id=p,peer_node=peer,peer_port=peer_port,
            role=role,plane=plane,peer_group=list(group),rate_bps=rate,coverage=c)
    return result


def classify(area, packet_bytes, width, rate, rule):
    if area<=0:return 'no_positive_wait'
    load=packet_bytes*8*1e9/(width*rate)
    if load>=rule['busy_proxy_min_fraction']:return 'waiting_high_service'
    if load<=rule['low_service_proxy_max_fraction']:return 'waiting_low_service'
    return 'waiting_intermediate_service'


def sequences(rows, rule):
    """Adjacent same-stratum windows; explicitly not continuous wait events."""
    groups=[];current=None
    for r in rows:
        state=r['stratum']
        if state=='no_positive_wait':
            if current:groups.append(current);current=None
            continue
        if current and (current['end_ns']!=r['start_ns'] or current['stratum']!=state):
            groups.append(current);current=None
        if current is None:
            current=dict(start_ns=r['start_ns'],end_ns=r['start_ns'],stratum=state,
                positive_windows=0,voq_byte_ns=0,fifo_byte_ns=0,packet_bytes=0)
        current['end_ns']=r['end_ns'];current['positive_windows']+=1
        for k in ('voq_byte_ns','fifo_byte_ns','packet_bytes'):current[k]+=r[k]
    if current:groups.append(current)
    return groups


def quantile_time(rows, fraction):
    total=sum(r['packet_bytes'] for r in rows)
    if not total:return None
    cumulative=0
    for r in rows:
        cumulative+=r['packet_bytes']
        if cumulative>=total*fraction:return r['end_ns']
    raise AssertionError('Invalid byte quantile')


def scan(archive, rule_path, output):
    if output.exists():raise ValueError('Output must be new')
    rule=read(rule_path);source=Archive(archive)
    names=['run/observed/'+n for n in ('public_config.json','storage.json',
        'records/queue_windows.csv.gz','records/port_windows.csv.gz')]
    names+=['run/inputs/network/backend.json','run/manifest.json','run/metrics/validation.json']
    files=dict(resolved_members(source,names))
    public,storage,network,manifest,validation=(json.loads(files[n]) for n in (names[0],names[1],names[4],names[5],names[6]))
    if not manifest['normal_exit'] or not validation['passed']:raise ValueError('Source did not pass')
    if storage['window_encoding']!='nonzero_with_declared_zero_windows':raise ValueError('No sparse zero declaration')
    metadata=ports(network,public);windows={};counts=Counter()
    for key,p in metadata.items():
        c=p['coverage']
        if c['period_ns']!=rule['window_ns'] or not c['start_local_ns']<=rule['scope_start_ns']<rule['scope_end_ns']<=c['end_local_ns']:
            raise ValueError('Declared capture does not cover the fixed analysis scope')
    for i,field in ((2,'queue_windows.csv'),(3,'port_windows.csv')):
        seen=set()
        for row in zipped_rows(files[names[i]]):
            key=row['switch_id'],row['port_id'];a=row['local_window_start_ns'];b=row['local_window_end_ns']
            if key not in metadata or (key,a) in seen:raise ValueError('Unknown/duplicate window')
            seen.add((key,a));counts[field]+=1
            c=metadata[key]['coverage']
            if not c['start_local_ns']<=a<b<=c['end_local_ns'] or (a-c['start_local_ns'])%c['period_ns'] or b!=min(a+c['period_ns'],c['end_local_ns']):
                raise ValueError('Invalid sparse window')
            if a<rule['scope_start_ns'] or b>rule['scope_end_ns']:continue
            w=windows.setdefault((key,a),dict(start_ns=a,end_ns=b,voq_byte_ns=0,fifo_byte_ns=0,
                voq_peak=0,fifo_peak=0,packet_bytes=0,packets=0))
            if i==2:
                for q in ('voq','fifo'):
                    w[q+'_byte_ns']=row[q+'_waiting_byte_ns'];w[q+'_peak']=row[q+'_waiting_peak_bytes']
            else:w['packet_bytes']=row['counted_packet_bytes'];w['packets']=row['counted_packets']
        if counts[field]!=storage['fields'][field]['records']:raise ValueError('Incomplete stored records')
    by_port=defaultdict(list);distribution=Counter()
    for (key,a),w in sorted(windows.items()):
        w['stratum']=classify(w['voq_byte_ns']+w['fifo_byte_ns'],w['packet_bytes'],
                              w['end_ns']-a,metadata[key]['rate_bps'],rule)
        by_port[key].append(w);distribution[metadata[key]['role']+':'+w['stratum']]+=1
    # Keep all quiet outlets explicitly, without materializing the zero grid.
    output.mkdir(parents=True)
    totals=Counter();selected={};plane_groups=defaultdict(list);all_groups=[];port_rows=[]
    excluded={tuple(p) for p in rule['excluded_historical_outlets']}
    with gzip.open(output/'windows.jsonl.gz','wt') as f:
        for key,p in sorted(metadata.items()):
            rows=by_port[key]
            values={k:sum(w[k] for w in rows) for k in ('voq_byte_ns','fifo_byte_ns','packet_bytes','packets')}
            totals.update(values)
            port_rows.append(dict(**p,**values,t50_ns=quantile_time(rows,.5),t90_ns=quantile_time(rows,.9),
                last_tx_window_end_ns=max((w['end_ns'] for w in rows if w['packet_bytes']),default=None)))
            plane_groups[tuple(p['peer_group'])].append(port_rows[-1])
            for w in rows:f.write(json.dumps(dict(switch_id=key[0],port_id=key[1],**w),separators=(',',':'))+'\n')
            for g in sequences(rows,rule):
                all_groups.append(dict(switch_id=key[0],port_id=key[1],role=p['role'],plane=p['plane'],**g))
                if key in excluded or g['positive_windows']<rule['consecutive_positive_windows']:continue
                stratum=p['role'],g['stratum']
                candidate=dict(switch_id=key[0],port_id=key[1],role=p['role'],plane=p['plane'],**g)
                order=lambda x:(x['start_ns'],x['switch_id'],x['port_id'])
                if stratum not in selected or order(candidate)<order(selected[stratum]):selected[stratum]=candidate
    pairs=[]
    for group,ps in plane_groups.items():
        for i,a in enumerate(ps):
            for b in ps[i+1:]:
                if not min(a['packet_bytes'],b['packet_bytes']):continue
                relative=abs(a['packet_bytes']-b['packet_bytes'])/max(a['packet_bytes'],b['packet_bytes'])
                if relative<=rule['comparable_plane_work_relative_difference']:
                    pairs.append(dict(peer_group=list(group),outlets=[[a['switch_id'],a['port_id']],[b['switch_id'],b['port_id']]],
                        planes=[a['plane'],b['plane']],packet_bytes=[a['packet_bytes'],b['packet_bytes']],
                        relative_work_difference=relative,t90_ns=[a['t90_ns'],b['t90_ns']],
                        t90_difference_ns=abs(a['t90_ns']-b['t90_ns'])))
    pairs.sort(key=lambda p:(-p['t90_difference_ns'],p['outlets']))
    save(output/'ports.json',port_rows)
    with gzip.open(output/'groups.jsonl.gz','wt') as f:
        for g in all_groups:f.write(json.dumps(g,separators=(',',':'))+'\n')
    save(output/'plane-pairs.json',pairs)
    logical=len(metadata)*(rule['scope_end_ns']-rule['scope_start_ns'])//rule['window_ns']
    save(output/'summary.json',dict(schema_version='issue-mechanism-normal-screen/1',
        rule=rule,rule_sha256=digest(rule_path),archive_sha256=source.index['archive_sha256'],members=source.used,
        scope_ns=[rule['scope_start_ns'],rule['scope_end_ns']],ports=len(metadata),
        roles=dict(Counter(p['role'] for p in metadata.values())),logical_windows=logical,
        stored_nonzero_windows=len(windows),declared_zero_windows=logical-len(windows),strata=dict(distribution),
        totals=dict(totals),adjacent_positive_groups=len(all_groups),
        checks_by_stratum={':'.join(k):v for k,v in selected.items()},comparable_plane_pairs=len(pairs),top_plane_pairs=pairs[:8],
        missing=['source device queue occupancy','credit eligibility and blocked intervals','exact serialization occupancy','per-packet complete waiting'],
        branch_labels_are_proxies_not_causes=True,program_sha256=digest(__file__),simulation_executed=False))
    print(json.dumps(dict(ports=len(metadata),windows=len(windows),checks=len(selected),plane_pairs=len(pairs))))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('archive','rule','output'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:scan(a.archive,a.rule,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],str(a.output)+'-resources',**host())
        if r['status']!='completed':raise SystemExit('Normal mechanism screen failed: '+r['status'])


if __name__=='__main__':main()
