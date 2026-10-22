"""Independently enumerate raw-source plans and windows; audit routes against real packets."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import heapq
from itertools import permutations
import json
from math import lcm
from pathlib import Path


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def dist(values):
    ordered=sorted(values);n=len(ordered)
    return dict(count=n,minimum=ordered[0],maximum=ordered[-1],p50=ordered[(n+1)//2-1],p95=ordered[(n*95+99)//100-1])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('prepared','planned','repeat_planned','evaluation','reference_result','output'):
        p.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    a=p.parse_args();inputs=a.prepared/'observed/inputs.json';data=load(inputs);truth=load(a.prepared/'truth/decode.json')
    provenance=load(a.prepared/'truth/sources.json');plan=load(a.planned/'plans.json');summary=load(a.evaluation/'summary.json')
    assert (a.planned/'plans.json').read_bytes()==(a.repeat_planned/'plans.json').read_bytes()
    assert sha(inputs)==plan['observed_sha256']==summary['observed_sha256']
    assert sha(a.planned/'plans.json')==summary['plans_sha256'] and sha(a.prepared/'truth/decode.json')==summary['decode_sha256']
    assert sha(a.evaluation/'windows.csv')==summary['csv_sha256'] and not plan['reads_decode_or_truth']
    assert data['independent_holdout_requests']==summary['independent_holdout_requests']==provenance['collection']['completed']
    x=load(a.reference_result);assert x['passed'] and x['drained'] and x['packet_capture']['complete']
    assert x['packet_capture']['omitted_events']==0
    raw_paths=defaultdict(set);by_uid=defaultdict(list)
    for e in x['packet_capture']['events']:by_uid[e[4]].append(e)
    for rows in by_uid.values():
        sent=[e for e in rows if e[3]==0];got=[e for e in rows if e[3]==1];protocol=sent[0][6]
        assert protocol in (17,252)
        nodes=tuple([e[1] for e in sent]+[got[-1][1]])
        src,dst=nodes[0]//2,nodes[-1]//2
        raw_paths[src,dst,protocol].add(nodes)
    fabric=data['fabric'];edges=fabric['edges'];port_index={(r['node'],r['peer']):i for i,r in enumerate(edges)}
    coefficients=[]
    for src in range(4):
        for dst in range(4):
            units=Counter();declared=fabric['pairs'][4*src+dst]
            if src!=dst:
                for key,bytes_,pair in (('data',15004,(src,dst,17)),('ack',720,(dst,src,252))):
                    assert len(raw_paths[pair])==1
                    nodes=next(iter(raw_paths[pair]));assert declared[key]['nodes']==list(nodes)
                    actual_ports=[]
                    for left,right in zip(nodes,nodes[1:]):
                        i=port_index[left,right];actual_ports.append(i);units[i]+=bytes_*(100000000000//edges[i]['rate_bps'])
                    assert declared[key]['ports']==actual_ports
            assert declared['port_units']==[units[i] for i in range(len(edges))];coefficients.append(units)
    def metrics(selections,owners,perm,weights):
        direct=[0]*16;load_by_port=Counter();compute=[0]*4;remote=0
        for src,rows in enumerate(selections):
            for token in rows:
                for e in token:compute[perm[owners[e]]]+=1;remote+=perm[owners[e]]!=src
                for dst in range(4):
                    if src==dst:continue
                    if any(perm[owners[e]]==dst for e in token):direct[4*src+dst]+=weights[src]
        for pair,copies in enumerate(direct):
            for i,units in coefficients[pair].items():load_by_port[i]+=copies*units
        return sum(direct),max(load_by_port.values(),default=0),max(compute),remote
    counts=Counter();raws=[];all_names=set()
    for ci,group in enumerate(provenance['groups']):
        requests=[]
        for source in group:
            file=Path(source['path']);assert sha(file)==source['sha256'];raw=load(file);assert len(raw)==129
            assert source['name'] not in all_names;all_names.add(source['name']);requests.append(raw)
        raws.append(requests)
        for li,layer in enumerate(range(3,61)):
            for src,raw in enumerate(requests):
                assert raw[0][str(layer)]==data['cohorts'][ci]['prefill'][src][li]
                assert [entry[str(layer)][0] for entry in raw[1:]]==truth['cohorts'][ci][li][src]
            for bi in range(3):
                pref=[raw[0][str(layer)][-10:] if bi==2 else raw[0][str(layer)] for raw in requests]
                if bi==0:owners=[e//64 for e in range(256)]
                else:
                    frequencies=Counter(e for rows in pref for token in rows for e in token)
                    heap=[(0,r,0) for r in range(4)];owners=[None]*256
                    for e in sorted(range(256),key=lambda e:(-frequencies[e],e)):
                        value,rank,assigned=heapq.heappop(heap);owners[e]=rank
                        if assigned+1<64:heapq.heappush(heap,(value+frequencies[e],rank,assigned+1))
                assert owners==data['cohorts'][ci]['base_maps'][bi][li] and Counter(owners)==dict.fromkeys(range(4),64)
                denominator=lcm(*(len(rows) for rows in pref));weights=[denominator//len(rows) for rows in pref]
                candidate=[]
                for perm in permutations(range(4)):
                    copies,peak,compute,remote=metrics(pref,owners,perm,weights)
                    candidate.append(dict(perm=list(perm),copies=copies,peak_units=peak));counts['candidate_scores']+=1
                row=plan['choices'][ci][bi][li];assert row['candidates']==candidate and row['denominator']==denominator
                # Sorting complete records with independent key tuples checks all ties.
                min_copy=sorted(candidate,key=lambda r:(r['copies'],tuple(r['perm'])))[0]['perm']
                min_port=sorted(candidate,key=lambda r:(r['peak_units'],r['copies'],tuple(r['perm'])))[0]['perm']
                assert row['selected']==[list(range(4)),min_copy,min_port]
                counts['layers_with_maps']+=1
    with (a.evaluation/'windows.csv').open(newline='') as f:
        reader=csv.DictReader(f)
        for ci,requests in enumerate(raws):
            for bi in range(3):
                baseline={}
                for pi in range(3):
                    saved=summary['conditions'][ci*9+bi*3+pi];copy_values=[];peaks=[];compute_values=[];expert_total=0;layers=[]
                    paired={key:dict(lower=0,equal=0,higher=0) for key in ('copies','peak_units')}
                    for li,layer in enumerate(range(3,61)):
                        owners=data['cohorts'][ci]['base_maps'][bi][li];perm=plan['choices'][ci][bi][li]['selected'][pi];total=0
                        for entry in range(1,129):
                            selections=[[raw[entry][str(layer)][0]] for raw in requests]
                            copies,peak,cmax,expert=metrics(selections,owners,perm,[1]*4)
                            row=next(reader);expected=dict(cohort=ci,base=bi,policy=pi,layer=layer,entry=entry,copies=copies,
                                remote_expert=expert,compute_max=cmax,peak_units=peak)
                            assert {k:int(v) for k,v in row.items()}==expected
                            if pi==0:baseline[li,entry]=(copies,peak,cmax)
                            assert cmax==baseline[li,entry][2]
                            for key,now,before in zip(paired,(copies,peak),baseline[li,entry][:2]):
                                paired[key]['lower' if now<before else 'higher' if now>before else 'equal']+=1
                            copy_values.append(copies);peaks.append(peak);compute_values.append(cmax);expert_total+=expert;total+=copies
                            counts['windows']+=1;counts['matrix_entries']+=16
                        layers.append(dict(layer=layer,copies=total,perm=perm))
                    assert saved['copies']==sum(copy_values) and saved['remote_expert_contributions']==expert_total
                    assert saved['copies_distribution']==dist(copy_values) and saved['compute_max']==dist(compute_values) and saved['peak_units']==dist(peaks)
                    assert saved['paired_with_same_group_identity']==paired and saved['by_layer']==layers
        assert next(reader,None)is None
    a.output.mkdir();save(a.output/'verification.json',dict(passed=True,**counts,raw_requests=len(all_names),
        static_data_ack_paths_checked=24,reference_result_sha256=sha(a.reference_result),summary_sha256=sha(a.evaluation/'summary.json'),
        observed_sha256=sha(inputs),plans_sha256=sha(a.planned/'plans.json'),plans_repeat_byte_identical=True,
        independent_holdout_requests=data['independent_holdout_requests'],new_network_executed=False,hardware_calibrated=False,
        completion_time_validated=False,goal_complete=False))
    print(json.dumps(dict(passed=True,**counts)))


if __name__=='__main__':main()
