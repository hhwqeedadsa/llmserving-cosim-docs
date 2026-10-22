"""Original Decode choices -> per-expert and rank-deduplicated Dispatch matrices.

These are hypothetical EP4 traffic operands, without arrivals, packets or timing.
Independent destination-range enumeration verifies every matrix entry.
"""
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

ROOT=Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def ratio(values):
    return max(values)*len(values)/sum(values) if sum(values) else None


def distribution(values):
    values=sorted(v for v in values if v is not None)
    if not values:
        return dict(count=0)
    # Nearest-rank empirical quantiles, no extrapolated confidence interval.
    def q(percent):
        return values[max(0,(len(values)*percent+99)//100-1)]
    return dict(count=len(values),minimum=values[0],p50=q(50),p95=q(95),maximum=values[-1])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30))
    resource.setrlimit(resource.RLIMIT_CPU,(60,60))
    started=time.monotonic()
    out=a.output.resolve()
    assert not out.exists() and shutil.disk_usage(out.parent).free>=8*2**30+8*2**20
    manifest_path=a.manifest.resolve()
    assert manifest_path.stat().st_size<=2**20
    manifest=json.loads(manifest_path.read_text())
    assert manifest['schema_version']=='moe-ub-route-manifest/1'
    assert manifest['source']['dataset_revision']=='27febb7b2d24169560a9f6f83d38eb53f94916cf'
    assert len(manifest['files'])==4 and manifest['model']['hidden_dim']==7168
    assert len(manifest['layers'])==61
    assert all(l['num_experts']==256 and l['top_k']==8 for l in manifest['layers'][3:])
    records,identities=[],[]
    total=0
    for item in manifest['files']:
        path=ROOT/item['file']['path']
        total+=path.stat().st_size
        assert total<=16*2**20 and sha(path)==item['file']['sha256']
        raw=json.loads(path.read_text())
        assert len(raw)==129
        for entry in raw[1:]:
            for layer in range(3,61):
                tokens=entry[str(layer)]
                assert len(tokens)==1 and len(tokens[0])==len(set(tokens[0]))==8
                assert all(type(e) is int and 0<=e<256 for e in tokens[0])
        records.append(raw)
        identities.append(dict(file_id=item['file_id'],path=item['file']['path'],sha256=sha(path)))
    assert len({i['sha256'] for i in identities})==4
    out.mkdir()
    old_total,new_total=0,0
    old_skew,new_skew,compute_skew=[],[],[]
    full_fanout=equal_incoming=zero_windows=0
    old_matrix=[[0]*4 for _ in range(4)]
    new_matrix=[[0]*4 for _ in range(4)]
    layer_rows=[]
    checked_entries=0
    with (out/'windows.csv').open('w',newline='') as f:
        fields=['source_entry','source_layer','old_remote_contributions','direct_remote_token_copies',
                'old_incoming_max_to_mean','direct_incoming_max_to_mean','expert_compute_rank_max_to_mean',
                'expert_max_count_in_window','direct_full_off_rank_fanout']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for layer in range(3,61):
            layer_old=layer_new=0
            for entry in range(1,129):
                choices=[r[entry][str(layer)][0] for r in records]
                matrix_old=[[0]*4 for _ in range(4)]
                matrix_new=[[0]*4 for _ in range(4)]
                compute=[0]*4
                experts=Counter()
                for src,selected in enumerate(choices):
                    experts.update(selected)
                    for expert in selected:
                        dst=expert//64
                        compute[dst]+=1
                        if src!=dst:
                            matrix_old[src][dst]+=1
                    for dst in {expert//64 for expert in selected}:
                        if src!=dst:
                            matrix_new[src][dst]+=1
                # Separate enumeration scans all expert ranges for every src/dst.
                for src in range(4):
                    for dst in range(4):
                        count=sum(dst*64<=expert<(dst+1)*64 for expert in choices[src]) if src!=dst else 0
                        assert matrix_old[src][dst]==count
                        assert matrix_new[src][dst]==int(count>0)
                        old_matrix[src][dst]+=count
                        new_matrix[src][dst]+=int(count>0)
                        checked_entries+=1
                old_n=sum(map(sum,matrix_old));new_n=sum(map(sum,matrix_new))
                old_in=[sum(matrix_old[src][dst] for src in range(4)) for dst in range(4)]
                new_in=[sum(matrix_new[src][dst] for src in range(4)) for dst in range(4)]
                assert sum(compute)==32 and max(new_in)<=3
                old_r,new_r,compute_r=ratio(old_in),ratio(new_in),ratio(compute)
                old_skew.append(old_r);new_skew.append(new_r);compute_skew.append(compute_r)
                old_total+=old_n;new_total+=new_n
                layer_old+=old_n;layer_new+=new_n
                full_fanout+=new_n==12
                equal_incoming+=len(set(new_in))==1
                zero_windows+=new_n==0
                writer.writerow(dict(source_entry=entry,source_layer=layer,old_remote_contributions=old_n,
                    direct_remote_token_copies=new_n,old_incoming_max_to_mean=old_r,
                    direct_incoming_max_to_mean=new_r,expert_compute_rank_max_to_mean=compute_r,
                    expert_max_count_in_window=max(experts.values()),direct_full_off_rank_fanout=new_n==12))
                assert time.monotonic()-started<60
            layer_rows.append(dict(source_layer=layer,windows=128,old_remote_contributions=layer_old,
                                   direct_remote_token_copies=layer_new))
    summary=dict(scope='original four-request Decode routes; conditional traffic operand accounting only',
        deployment=dict(ranks=4,batch_per_rank=1,expert_placement='contiguous_equal_64_per_rank',
            precision='BF16 assumed for hidden-byte accounting',cross_rank_path='RDMA-only assumed',
            synchronization='equal Decode entry; no observed request arrival or launch times'),
        windows=58*128,individual_token_layer_rows=4*58*128,independent_matrix_entries_checked=checked_entries,
        old_per_expert_remote_dispatch_contributions=old_total,direct_remote_dispatch_token_copies=new_total,
        old_hidden_operand_bytes=old_total*7168*2,direct_hidden_operand_bytes=new_total*7168*2,
        hidden_byte_scope='hidden elements only; metadata, padding, counters, barriers, responses and wire headers excluded',
        full_off_rank_fanout_windows=full_fanout,equal_incoming_direct_windows=equal_incoming,
        zero_remote_windows=zero_windows,
        per_window_incoming_max_to_mean=dict(per_expert=distribution(old_skew),direct=distribution(new_skew)),
        per_window_compute_expert_inputs_rank_max_to_mean=distribution(compute_skew),
        aggregate_dispatch_operand_copies=dict(per_expert=old_matrix,direct=new_matrix),
        by_layer=layer_rows,packet_execution=False,latency_predicted=False,
        is_training_trace=False,is_production_sample=False,full_model_deployment_verified=False)
    save(out/'summary.json',summary)
    save(out/'provenance.json',dict(manifest_path=str(manifest_path.relative_to(ROOT)),manifest_sha256=sha(manifest_path),
        source=manifest['source'],files=identities,
        runtime_target='DeepEP f6aa38fe06cd4a0489e7730365d4e0114970a2f3 direct fresh-routing Dispatch',
        source_reference='docs/review_artifacts/deepep_modern_20261022/sources.json'))
    usage=resource.getrusage(resource.RUSAGE_SELF)
    assert sum(p.stat().st_size for p in out.iterdir())<=8*2**20 and usage.ru_maxrss<=2**20
    save(out/'execution.json',dict(argv=sys.argv,cwd=str(ROOT),state='completed',
        wall_seconds=time.monotonic()-started,peak_rss_kib=usage.ru_maxrss,
        budgets=dict(wall_seconds=60,rss_bytes=2**30,address_bytes=2*2**30,output_bytes=8*2**20),
        summary_sha256=sha(out/'summary.json'),windows_sha256=sha(out/'windows.csv')))
    print(json.dumps({k:v for k,v in summary.items() if k not in ('by_layer','aggregate_dispatch_operand_copies')},ensure_ascii=False))


if __name__=='__main__':
    main()
