"""Analyze only prepared observed routing/placement tensors, never provenance truth."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import resource
import shutil
import sys
import time


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v): p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def distribution(values):
    values = sorted(v for v in values if v is not None)
    if not values: return dict(count=0)
    return dict(count=len(values),minimum=values[0],p50=values[(len(values)+1)//2-1],
                p95=values[(len(values)*95+99)//100-1],maximum=values[-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observed',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    a = parser.parse_args(); start = time.monotonic(); folder = a.observed.resolve(); out = a.output.resolve()
    resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30)); resource.setrlimit(resource.RLIMIT_CPU,(60,60))
    assert not out.exists() and out.parent.is_dir() and shutil.disk_usage(out.parent).free >= 8*2**30+16*2**20
    assert (folder/'index.json').stat().st_size <= 65536
    index = json.loads((folder/'index.json').read_text())
    assert index['schema_version']=='moe-route-controls-observed/1'
    assert (index['ranks'],index['experts'],index['top_k'],index['decode_entries']) == (4,256,8,128)
    assert index['source_layers']==list(range(3,61)) and len(index['conditions'])==8
    assert len({c['id'] for c in index['conditions']})==8
    cache = {}; total = 0
    for name, info in index['artifacts'].items():
        assert Path(name).name==name and name.endswith('.json')
        p = folder/name; total += p.stat().st_size
        assert total <= 16*2**20 and p.stat().st_size==info['bytes'] and sha(p)==info['sha256']
        cache[name]=json.loads(p.read_text())
    out.mkdir(); results = []
    with (out/'windows.csv').open('w',newline='') as f:
        columns = 'condition layer entry matrix_bits direct_copies expert_remote_contributions local_token_copies incoming_max_to_mean compute_max_to_mean expert_max_selected'.split()
        writer = csv.DictWriter(f,fieldnames=columns); writer.writeheader()
        for condition in index['conditions']:
            tensor = cache[condition['routes']]; placement = cache[condition['placement']]
            assert tensor['schema_version']=='moe-route-control-tensor/1' and placement['schema_version']=='moe-route-control-placement/1'
            selections,owners = tensor['selections'],placement['owners']
            assert len(selections)==len(owners)==58
            direct_total=expert_total=local_total=equal=full=zeros=0; matrix_total=[0]*16; expert_matrix=[0]*16
            incoming_ratios=[]; compute_ratios=[]; expert_maxima=[]; layers=[]
            for li, source_layer in enumerate(index['source_layers']):
                assert len(selections[li])==4 and len(owners[li])==256
                assert all(type(v) is int and 0 <= v < 4 for v in owners[li])
                assert Counter(owners[li])==Counter({0:64,1:64,2:64,3:64})
                assert all(len(rows)==128 for rows in selections[li])
                layer_direct=layer_expert=0; layer_ratios=[]
                for entry in range(128):
                    matrix=[0]*16; compute=[0]*4; selected_all=[]; local=old=0
                    for src in range(4):
                        choices=selections[li][src][entry]
                        assert len(choices)==len(set(choices))==8 and all(type(e) is int and 0<=e<256 for e in choices)
                        destinations=set()
                        for expert in choices:
                            dst=owners[li][expert]; destinations.add(dst); compute[dst]+=1
                            if src!=dst: old+=1; expert_matrix[4*src+dst]+=1
                        for dst in destinations:
                            if src==dst: local+=1
                            else: matrix[4*src+dst]=1
                        selected_all.extend(choices)
                    copies=sum(matrix); incoming=[sum(matrix[4*s+d] for s in range(4)) for d in range(4)]
                    assert 0 <= copies <= 12 and sum(compute)==32
                    incoming_ratio=max(incoming)*4/copies if copies else None; compute_ratio=max(compute)/8
                    hottest=max(Counter(selected_all).values())
                    writer.writerow(dict(condition=condition['id'],layer=source_layer,entry=entry+1,
                        matrix_bits=''.join(map(str,matrix)),direct_copies=copies,
                        expert_remote_contributions=old,local_token_copies=local,
                        incoming_max_to_mean=incoming_ratio,compute_max_to_mean=compute_ratio,expert_max_selected=hottest))
                    direct_total+=copies;expert_total+=old;local_total+=local
                    equal+=len(set(incoming))==1;full+=copies==12;zeros+=copies==0
                    incoming_ratios.append(incoming_ratio);compute_ratios.append(compute_ratio);expert_maxima.append(hottest)
                    layer_direct+=copies;layer_expert+=old;layer_ratios.append(incoming_ratio)
                    matrix_total=[a+b for a,b in zip(matrix_total,matrix)]
                layers.append(dict(layer=source_layer,direct_copies=layer_direct,expert_remote_contributions=layer_expert,
                                   incoming_max_to_mean=distribution(layer_ratios)))
                assert time.monotonic()-start < 55
            results.append(dict(condition=condition['id'],windows=7424,direct_copies=direct_total,
                expert_remote_contributions=expert_total,local_token_copies=local_total,
                all_destination_token_copies=direct_total+local_total,
                aggregate_direct_matrix=matrix_total,aggregate_expert_matrix=expert_matrix,
                incoming_max_to_mean=distribution(incoming_ratios),compute_max_to_mean=distribution(compute_ratios),
                expert_max_selected=distribution(expert_maxima),equal_incoming_windows=equal,
                full_remote_fanout_windows=full,zero_remote_windows=zeros,by_layer=layers))
    save(out/'summary.json',dict(schema_version='moe-route-controls-analysis/1',conditions=results,
        observed_index_sha256=sha(folder/'index.json'),windows=7424,conditions_count=8,
        scope='four correlated Decode requests under declared EP4 placement/alignment; traffic operands only',
        network_executed=False,latency_predicted=False,hardware_calibrated=False,
        optimizer_evaluated=False,training_or_production_generalization=False,
        reads_truth_or_raw_sources=False))
    assert sum(p.stat().st_size for p in out.iterdir()) <= 16*2**20
    save(out/'execution.json',dict(argv=sys.argv,cwd=str(Path.cwd()),completed=True,
        wall_seconds=time.monotonic()-start,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        script_sha256=sha(Path(__file__)),summary_sha256=sha(out/'summary.json'),windows_sha256=sha(out/'windows.csv')))
    print(json.dumps(dict(completed=True,conditions=8,window_rows=8*7424,
        direct_copies={r['condition']:r['direct_copies'] for r in results})))


if __name__=='__main__':main()
