"""Prepare, plan, and evaluate fixed Prefill-only group-to-rank baselines."""
import argparse
from collections import Counter, deque
from copy import deepcopy
import csv
import hashlib
from itertools import permutations
import json
from math import lcm
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
BASES=('contiguous','prefill_all_lpt','prefill_tail10_lpt')
POLICIES=('identity','min_remote','min_port_service')
FIXED_PLACEMENTS_SHA='dcdf9220498839182591250f114d85f75bbe851e376c9bf828a82c384964d3e7'


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,separators=(',',':'))+'\n')


def hash32(words,seed):
    mask=2**32-1;h=seed
    def rol(x,b):return ((x<<b)|(x>>(32-b)))&mask
    for k in words:
        k=(rol(k*0xcc9e2d51&mask,15)*0x1b873593)&mask
        h=rol(h^k,13);h=(h*5+0xe6546b64)&mask
    h^=12;h^=h>>16;h=h*0x85ebca6b&mask;h^=h>>13;h=h*0xc2b2ae35&mask;return h^(h>>16)


def fabric_model(topology):
    adj={n['id']:[] for n in topology['nodes']};ports={}
    for link in topology['links']:
        for n,p,peer,pp in ((link['src'],link['src_port'],link['dst'],link['dst_port']),
                            (link['dst'],link['dst_port'],link['src'],link['src_port'])):
            adj[n].append((peer,p));ports[n,p]=dict(node=n,port=p,peer=peer,peer_port=pp,
                tier=link['tier'],rate_bps=link['rate_bps'],rate_units=100000000000//link['rate_bps'])
    edges=[ports[key] for key in sorted(ports)];index={(r['node'],r['port']):i for i,r in enumerate(edges)}
    def route(src,dst,wire):
        distance={dst:0};queue=deque([dst])
        while queue:
            n=queue.popleft()
            for peer,p in adj[n]:
                if peer not in distance:
                    distance[peer]=distance[n]+1
                    if peer>=8:queue.append(peer)
        n=src;nodes=[src];links=[]
        while n!=dst:
            options=[(peer,p) for peer,p in adj[n] if distance[peer]==distance[n]-1]
            choice=0 if n<8 else hash32([0x0b000001+src*32,0x0b000001+dst*32,wire|(wire<<16)],11+n)%len(options)
            peer,p=options[choice];links.append(index[n,p]);nodes.append(peer);n=peer
        return dict(nodes=nodes,ports=links)
    pairs=[]
    for src in range(4):
        for dst in range(4):
            if src==dst:pairs.append(dict(src=src,dst=dst,port_units=[0]*len(edges)));continue
            wire=21000+4*src+dst;data=route(src*2,dst*2,wire);ack=route(dst*2,src*2,wire);units=[0]*len(edges)
            for path,bytes_ in ((data,15004),(ack,720)):
                for i in path['ports']:units[i]+=bytes_*edges[i]['rate_units']
            pairs.append(dict(src=src,dst=dst,wire_port=wire,data=data,ack=ack,port_units=units))
    return dict(edges=edges,pairs=pairs,token_payload_bytes=14464,data_model_bytes=15004,ack_model_bytes=720,
        service_unit_ns='0.08',seed=11,scope='Declared static serialized service demand, not completion time')


def base_maps(prefill):
    maps=[[[e//64 for e in range(256)] for _ in range(58)]]
    for tail in (False,True):
        layers=[]
        for li in range(58):
            counts=Counter(e for source in prefill for token in (source[li][-10:] if tail else source[li]) for e in token)
            owners=[None]*256;loads=[0]*4;used=[0]*4
            for e in sorted(range(256),key=lambda e:(-counts[e],e)):
                rank=min((r for r in range(4) if used[r]<64),key=lambda r:(loads[r],r))
                owners[e]=rank;loads[rank]+=counts[e];used[rank]+=1
            assert used==[64]*4;layers.append(owners)
        maps.append(layers)
    return maps


def prepare(a):
    out=a.output;out.mkdir();observed=out/'observed';truth=out/'truth';observed.mkdir();truth.mkdir()
    manifest=load(a.manifest);assert manifest['source']['dataset_revision']=='27febb7b2d24169560a9f6f83d38eb53f94916cf'
    assert sha(a.placements)==FIXED_PLACEMENTS_SHA
    source_groups=[[dict(path=str((a.source_root/r['file']['path']).resolve()),sha256=r['file']['sha256'],
        name=Path(r['file']['path']).name) for r in manifest['files']]];names=['development-four']
    collection=load(a.holdout/'collection.json')
    if collection['completed']:
        rows=[]
        for selected in collection['selection']:
            file=a.holdout/Path(selected['path']).name
            assert file.name not in {r['name'] for r in source_groups[0]}
            digest=next(r['sha256'] for r in collection['requests'] if r['file']==file.name)
            rows.append(dict(path=str(file.resolve()),sha256=digest,name=file.name))
        assert len(rows)==4;source_groups.append(rows);names.append('new-request-four')
    cohorts=[];decode=[];total=0
    for name,sources in zip(names,source_groups):
        assert len(sources)==4;prefill=[];future=[[] for _ in range(58)];lengths=[]
        for source in sources:
            file=Path(source['path']);assert sha(file)==source['sha256'];total+=file.stat().st_size;assert total<32*2**20
            raw=load(file);assert len(raw)==129;n=len(raw[0]['3']);lengths.append(n);src_pre=[]
            for li,layer in enumerate(range(3,61)):
                src_pre.append(raw[0][str(layer)]);assert len(src_pre[-1])==n and n>0
                step_rows=[entry[str(layer)] for entry in raw[1:]];assert all(len(r)==1 for r in step_rows)
                for token in [*src_pre[-1],*(r[0] for r in step_rows)]:
                    assert len(token)==len(set(token))==8 and all(type(e)is int and 0<=e<256 for e in token)
                future[li].append([r[0] for r in step_rows])
            prefill.append(src_pre)
        maps=base_maps(prefill)
        if name=='development-four':assert {base:maps[i] for i,base in enumerate(BASES)}==load(a.placements)['placements']
        cohorts.append(dict(name=name,prefill=prefill,prefill_lengths=lengths,base_maps=maps));decode.append(future)
    topology=deepcopy(load(ROOT/'examples/scaleout_rdma/small-clos-100g.json'));topology['deployment']['gpu_type']='NONE'
    for tier in ('tor_agg','agg_core','tor_core'):topology['links'][tier]['rate_bps']=25000000000
    save(observed/'topology-config.json',topology)
    command=[sys.executable,'-m','moe_ub_cosim','scaleout','topology','--config',str(observed/'topology-config.json'),'--output',str(observed/'topology')]
    child=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=30)
    save(truth/'topology-command.json',dict(command=command,cwd=str(ROOT),exit_code=child.returncode,stdout=child.stdout,stderr=child.stderr));assert child.returncode==0
    save(observed/'inputs.json',dict(cohorts=cohorts,base_names=BASES,policy_names=POLICIES,
        fabric=fabric_model(load(observed/'topology/topology.json')),independent_holdout_requests=len(cohorts)==2))
    save(truth/'decode.json',dict(cohorts=decode));save(truth/'sources.json',dict(groups=source_groups,names=names,
        manifest_sha256=sha(a.manifest),original_bytes=total,collection=collection,source_rank_assignment='listed order'))
    save(truth/'identity.json',dict(observed_sha256=sha(observed/'inputs.json'),decode_sha256=sha(truth/'decode.json')))
    print(json.dumps(dict(cohorts=names,holdout_available=len(cohorts)==2,topology_cli_completed=True)))


def demand(prefill,owners,perm):
    denom=lcm(*(len(rows) for rows in prefill));matrix=[0]*16
    for src,rows in enumerate(prefill):
        weight=denom//len(rows)
        for token in rows:
            for dst in {perm[owners[e]] for e in token}-{src}:matrix[4*src+dst]+=weight
    return denom,matrix


def service(matrix,fabric):
    ports=[0]*len(fabric['edges'])
    for copies,pair in zip(matrix,fabric['pairs']):
        if copies:
            for i,units in enumerate(pair['port_units']):ports[i]+=copies*units
    return max(ports)


def plan(a):
    inputs=a.observed/'inputs.json';data=load(inputs);choices=[]
    for cohort in data['cohorts']:
        bases=[]
        for bi in range(3):
            layers=[]
            for li in range(58):
                pref=[src[li][-10:] if bi==2 else src[li] for src in cohort['prefill']];candidates=[]
                for perm in permutations(range(4)):
                    denom,matrix=demand(pref,cohort['base_maps'][bi][li],perm)
                    candidates.append(dict(perm=list(perm),copies=sum(matrix),peak_units=service(matrix,data['fabric'])))
                selected=[candidates[0],min(candidates,key=lambda c:(c['copies'],c['perm'])),
                    min(candidates,key=lambda c:(c['peak_units'],c['copies'],c['perm']))]
                layers.append(dict(denominator=denom,candidates=candidates,selected=[c['perm'] for c in selected]))
            bases.append(layers)
        choices.append(bases)
    a.output.mkdir();save(a.output/'plans.json',dict(observed_sha256=sha(inputs),choices=choices,
        reads_decode_or_truth=False,objective='Per-source normalized Prefill; identity / min remote / min physical port service'))
    print(json.dumps(dict(cohorts=len(choices),candidate_scores=len(choices)*3*58*24,plans=len(choices)*9*58)))


def distribution(counter):
    n=sum(counter.values());ordered=sorted(counter);result=dict(count=n,minimum=ordered[0],maximum=ordered[-1])
    for name,percent in (('p50',50),('p95',95)):
        target=(n*percent+99)//100;seen=0
        for value in ordered:
            seen+=counter[value]
            if seen>=target:result[name]=value;break
    return result


def evaluate(a):
    data=load(a.prepared/'observed/inputs.json');planned=load(a.planned/'plans.json');identity=load(a.prepared/'truth/identity.json')
    assert planned['observed_sha256']==identity['observed_sha256']==sha(a.prepared/'observed/inputs.json')
    assert identity['decode_sha256']==sha(a.prepared/'truth/decode.json');future=load(a.prepared/'truth/decode.json')['cohorts']
    a.output.mkdir();results=[]
    with (a.output/'windows.csv').open('w',newline='') as f:
        fields='cohort base policy layer entry copies remote_expert compute_max peak_units'.split()
        writer=csv.DictWriter(f,fieldnames=fields,lineterminator='\n');writer.writeheader()
        for ci,cohort in enumerate(data['cohorts']):
            for bi in range(3):
                baseline={}
                for pi in range(3):
                    copies_hist=Counter();compute_hist=Counter();peak_hist=Counter();remote=0;layers=[]
                    paired={key:dict(lower=0,equal=0,higher=0) for key in ('copies','peak_units')}
                    for li in range(58):
                        owners=cohort['base_maps'][bi][li];perm=planned['choices'][ci][bi][li]['selected'][pi];layercopies=0
                        for step in range(128):
                            rows=[future[ci][li][src][step] for src in range(4)]
                            _,matrix=demand([[row] for row in rows],owners,perm)
                            compute=[0]*4;expert=0
                            for src,row in enumerate(rows):
                                for e in row:dst=perm[owners[e]];compute[dst]+=1;expert+=dst!=src
                            copies=sum(matrix);peak=service(matrix,data['fabric']);cmax=max(compute)
                            if pi==0:baseline[li,step]=(copies,peak,cmax)
                            assert cmax==baseline[li,step][2]
                            for key,value,before in zip(paired,(copies,peak),baseline[li,step][:2]):
                                paired[key]['lower' if value<before else 'higher' if value>before else 'equal']+=1
                            writer.writerow(dict(cohort=ci,base=bi,policy=pi,layer=li+3,entry=step+1,copies=copies,
                                remote_expert=expert,compute_max=cmax,peak_units=peak))
                            copies_hist[copies]+=1;compute_hist[cmax]+=1;peak_hist[peak]+=1;remote+=expert;layercopies+=copies
                        layers.append(dict(layer=li+3,copies=layercopies,perm=perm))
                    results.append(dict(cohort=ci,cohort_name=cohort['name'],base=bi,base_name=BASES[bi],policy=pi,policy_name=POLICIES[pi],
                        copies=sum(v*n for v,n in copies_hist.items()),remote_expert_contributions=remote,
                        copies_distribution=distribution(copies_hist),compute_max=distribution(compute_hist),
                        peak_units=distribution(peak_hist),paired_with_same_group_identity=paired,by_layer=layers))
    save(a.output/'summary.json',dict(conditions=results,windows_per_condition=7424,plans_sha256=sha(a.planned/'plans.json'),
        observed_sha256=identity['observed_sha256'],decode_sha256=identity['decode_sha256'],csv_sha256=sha(a.output/'windows.csv'),
        independent_holdout_requests=data['independent_holdout_requests'],network_executed=False,hardware_calibrated=False,
        completion_time_predicted=False,objective_is_known_baseline=True))
    print(json.dumps([{k:r[k] for k in ('cohort','base','policy','copies','peak_units')} for r in results]))


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='mode',required=True)
    for mode,args in (('prepare',('manifest','placements','holdout','source_root','output')),
                      ('plan',('observed','output')),('evaluate',('prepared','planned','output'))):
        child=sub.add_parser(mode)
        for arg in args:child.add_argument('--'+arg.replace('_','-'),type=Path,required=True)
    a=p.parse_args();a.output=a.output.resolve()
    assert not a.output.exists() and shutil.disk_usage(a.output.parent).free>=8*2**30+32*2**20
    globals()[a.mode](a)
    assert sum(file.stat().st_size for file in a.output.rglob('*') if file.is_file())<=32*2**20


if __name__=='__main__':main()
