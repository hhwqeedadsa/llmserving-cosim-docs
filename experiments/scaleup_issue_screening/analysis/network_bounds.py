"""Restricted offline candidate bounds. Reads only explicitly supplied inputs.

The source-end contract is uniform by operation class, never selected from
per-transfer truth. Port statistics establish waiting, not its application ID.
"""
import argparse
from collections import defaultdict
from pathlib import Path

from .network_information import judge_input as old_judge
from .workflow import digest, read, save

RULE = dict(version='source-end-and-last-window/1',
    bounds='per positive src/dst pair: floor(src_start)<window_end; ceil(dst_end)>window_start; '
           'and ceil(src_end)>window_start only for certified remote-delivery completion',
    rounding='outward to the same public clock resolution in every condition',
    aggregation='union of still-possible sources; never use destination start as packet lower bound',
    mapping='last application transmission START window end; preserve destination-end capture guard',
    unknown='missing records and uncovered future remain possible; empty queue is not final completion',
    scope='offline application operation candidates, not protocol retirement or causal business loss')


def simplify(data, relation):
    # Reuse original strict relation checks without reading any files.
    old_judge(data, relation)
    operations={e['operation'] for e in data['endpoints']}
    ports={(w['switch_id'],w['port_id']) for w in data['windows']}
    last={}
    for r in relation['records']:
        if r['operation'] not in operations or (r['switch_id'],r['port_id']) not in ports:
            raise ValueError('Relation has an unknown operation/port')
        key=r['operation'],r['switch_id'],r['port_id']
        last[key]=max(last.get(key,0),r['end_ns'])
    return dict(schema_version='issue-network-last-window/1',input_sha256=relation['input_sha256'],
        start_ns=relation['start_ns'],end_ns=relation['end_ns'],
        records=[dict(operation=o,switch_id=s,port_id=p,last_tx_window_end_ns=last.get((o,s,p)),
                      capture_complete=True) for o in sorted(operations) for s,p in sorted(ports)])


def boundaries(data, extra):
    if extra is None:return None,{}
    if extra['schema_version']=='issue-network-ideal-relation/1':extra=simplify(data,extra)
    if (set(extra)!={'schema_version','input_sha256','start_ns','end_ns','records'} or
        extra['schema_version']!='issue-network-last-window/1' or
        extra['start_ns']!=min(w['start_ns'] for w in data['windows']) or
        extra['end_ns']!=max(w['end_ns'] for w in data['windows'])):
        raise ValueError('Simplified relation changes schema or coverage')
    result={};ops={e['operation'] for e in data['endpoints']}
    ports={(w['switch_id'],w['port_id']) for w in data['windows']}
    for r in extra['records']:
        if set(r)!={'operation','switch_id','port_id','last_tx_window_end_ns','capture_complete'}:
            raise ValueError('Unexpected simplified boundary field')
        key=r['operation'],r['switch_id'],r['port_id'];last=r['last_tx_window_end_ns']
        if key in result or key[0] not in ops or key[1:] not in ports or type(r['capture_complete']) is not bool:
            raise ValueError('Invalid simplified boundary identity/completeness')
        if last is not None and (type(last) is not int or not extra['start_ns']<last<=extra['end_ns']):
            raise ValueError('Simplified last window outside coverage')
        result[key]=r
    return extra,result


def judge_input(data, semantics, mode='strong', extra=None):
    old_judge(data)  # Original input allowlist; no raw paths/labels accepted.
    if mode not in ('old','strong'):raise ValueError('Unknown baseline')
    if (set(semantics)!={'schema_version','input_sha256','clock','operations'} or
        semantics['schema_version']!='issue-network-end-semantics/1' or
        semantics['clock']!='aligned_simulation_ns'):
        raise ValueError('Unexpected semantic certificate')
    applicable={}
    for r in semantics['operations']:
        if (set(r)!={'operation','source_end_bounds_remote_delivery'} or
            type(r['source_end_bounds_remote_delivery']) is not bool or r['operation'] in applicable):
            raise ValueError('Invalid semantic class')
        applicable[r['operation']]=r['source_end_bounds_remote_delivery']
    ends={(r['operation'],r['rank']):r for r in data['endpoints']}
    ops=sorted({k[0] for k in ends})
    if set(applicable)!=set(ops):raise ValueError('Incomplete semantic classes')
    resolution=data['time_resolution_ns']
    if type(resolution) is not int or resolution<=0:raise ValueError('Invalid clock resolution')
    floor=lambda n:n//resolution*resolution
    ceil=lambda n:(n+resolution-1)//resolution*resolution
    by_dst=defaultdict(list)
    for f in data['flows']:
        if f['payload_bytes']>0 and f['src']!=f['dst']:by_dst[f['dst']].append(f)
    extra,last=boundaries(data,extra);result=[]
    for w in data['windows']:
        possible=set();reasons={o:dict(positive_sources=0,source_not_started=0,destination_ended=0,
            source_ended=0,possible_sources=[]) for o in ops}
        for f in by_dst[w['receiver']]:
            o=f['operation'];r=reasons[o];r['positive_sources']+=1
            src,dst=ends[o,f['src']],ends[o,f['dst']]
            before=floor(src['start_ns'])>=w['end_ns']
            dest=ceil(dst['end_ns'])<=w['start_ns']
            source=mode=='strong' and applicable[o] and ceil(src['end_ns'])<=w['start_ns']
            r['source_not_started']+=before;r['destination_ended']+=dest;r['source_ended']+=source
            if not (before or dest or source):r['possible_sources'].append(f['src']);possible.add(o)
        for o in ops:
            r=reasons[o]
            r['possible_sources']=sorted(set(r['possible_sources']))
            r['boundary_state']='not_supplied';r['boundary_excluded']=False
            r['offline_capture_horizon_ns']=0
            if extra is None or o not in possible:continue
            row=last.get((o,w['switch_id'],w['port_id']))
            r['offline_capture_horizon_ns']=max(0,extra['end_ns']-w['end_ns'])
            if row is None or not row['capture_complete']:
                r['boundary_state']='missing_records';continue
            if ceil(ends[o,w['receiver']]['end_ns'])>extra['end_ns']:
                r['boundary_state']='future_uncovered';continue
            b=row['last_tx_window_end_ns']
            r['boundary_state']='complete_no_transmission' if b is None else 'last_transmission_window'
            r['last_tx_window_end_ns']=b
            if b is None or b<=w['start_ns']:
                possible.remove(o);r['boundary_excluded']=True
        result.append(dict(sample=w['sample'],possible_operations=sorted(possible),
            endpoint_only_waiting='unknown',plus_queue_waiting=bool(w['voq_byte_ns'] or w['fifo_byte_ns']),
            operation_reasons=reasons))
    return result


def judge(input_path, semantics_path, output, mode='strong', relation=None):
    if output.exists():raise ValueError('Judgment output must be new')
    data,semantics=read(input_path),read(semantics_path)
    extra=read(relation) if relation is not None else None
    identity=digest(input_path)
    if semantics['input_sha256']!=identity or (extra is not None and extra['input_sha256']!=identity):
        raise ValueError('Restricted input identity differs')
    save(output,dict(rule=RULE,mode=mode,input_sha256=identity,semantics_sha256=digest(semantics_path),
        extra_sha256=digest(relation) if relation is not None else None,program_sha256=digest(__file__),
        samples=judge_input(data,semantics,mode,extra)))


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    a=sub.add_parser('judge')
    for n in ('input','semantics','output'):a.add_argument('--'+n,type=Path,required=True)
    a.add_argument('--mode',choices=('old','strong'),default='strong')
    a.add_argument('--relation',type=Path,help='Prepared ideal relation OR last-window boundary, never raw truth')
    a=sub.add_parser('simplify')
    for n in ('input','relation','output'):a.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args()
    if a.action=='judge':judge(a.input,a.semantics,a.output,a.mode,a.relation)
    else:
        if a.output.exists():raise ValueError('Boundary output must be new')
        data,relation=read(a.input),read(a.relation)
        if relation['input_sha256']!=digest(a.input):raise ValueError('Relation identity differs')
        save(a.output,simplify(data,relation))


if __name__=='__main__':main()
