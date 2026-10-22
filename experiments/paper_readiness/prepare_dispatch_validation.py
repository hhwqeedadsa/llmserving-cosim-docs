"""Fixed new packet-evaluation windows and ECMP conditions; read raw data only here/checker."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[2]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def load(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','placements','output'):parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args();out=args.output.resolve();assert not out.exists()
    assert sha(args.placements)=='dcdf9220498839182591250f114d85f75bbe851e376c9bf828a82c384964d3e7'
    cfg=dict(schema_version='moe-dispatch-validation/1',source_windows=[dict(layer=l,entry=e) for l,e in ((4,5),(4,6),(32,5),(32,6))],
        placements_sha256=sha(args.placements),logical_to_gpu=[0,2,4,6],token_bytes=14464,queue_depth=1,wire_port_base=21000,
        placement_conditions=['contiguous','prefill_all_lpt','prefill_tail10_lpt'],
        network_profiles={f's{seed}-{name}':rate for seed in (11,29,47) for name,rate in [('all100g',100000000000),('upper25g',25000000000)]},
        network_seeds={f's{seed}-{name}':seed for seed in (11,29,47) for name in ('all100g','upper25g')})
    maps=load(args.placements)['placements'];manifest=load(args.manifest);assert len(manifest['files'])==4
    choices=[];total=0
    for item in manifest['files']:
        path=ROOT/item['file']['path'];total+=path.stat().st_size
        assert total<16*1024**2 and sha(path)==item['file']['sha256']
        raw=load(path);rows=[raw[w['entry']][str(w['layer'])][0] for w in cfg['source_windows']]
        assert all(len(set(row))==len(row)==8 and all(type(e)is int and 0<=e<256 for e in row) for row in rows)
        choices.append(rows)
    out.mkdir();observed=out/'observed';truth=out/'truth';observed.mkdir();truth.mkdir()
    topology=load(ROOT/'examples/scaleout_rdma/small-clos-100g.json');topology['deployment']['gpu_type']='NONE'
    protocol=load(ROOT/'examples/scaleout_rdma/rdma.json')
    protocol.update(schema_version='moe-scaleout-rdma/2',queue_metrics=True,
        packet_capture=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000),
        limits=dict(max_gpus=32,max_switches=16,max_links=256,max_messages=8192,max_queue_events=200000,
            queue_capture='summary',max_sim_ns=5000000,wall_seconds=60,rss_bytes=1024**3,address_bytes=2*1024**3,
            artifact_bytes=64*1024**2,result_json_bytes=16*1024**2))
    protocol['ecn_by_rate']['25000000000']=deepcopy(protocol['ecn_by_rate']['100000000000'])
    for name,rate in cfg['network_profiles'].items():
        physical=deepcopy(topology)
        for tier in ('tor_agg','agg_core','tor_core'):physical['links'][tier]['rate_bps']=rate
        save(observed/f'topology-{name}.json',physical)
        configured=deepcopy(protocol);configured['seed']=cfg['network_seeds'][name]
        save(observed/f'protocol-{name}.json',configured)
    streams=[dict(id=4*s+d,src=2*s,dst=2*d,plane=0,wire_port=21000+4*s+d,source_nic=1,destination_nic=1,queue_depth=1)
        for s in range(4) for d in range(4) if s!=d]
    work_truth={}
    for name in cfg['placement_conditions']:
        work=[];previous=[];details=[]
        for step,window in enumerate(cfg['source_windows']):
            owners=maps[name][window['layer']-3];assert [owners.count(r) for r in range(4)]==[64]*4
            current=[]
            for src in range(4):
                for dst in sorted({owners[e] for e in choices[src][step]}-{src}):
                    ident=16*step+4*src+dst;current.append(ident)
                    work.append(dict(id=ident,stream=4*src+dst,bytes=14464,after=[dict(work=w,event='acked') for w in previous]))
                    details.append(dict(work=ident,layer=window['layer'],entry=window['entry'],source_rank=src,destination_rank=dst))
            assert current;previous=current
        save(observed/f'work-{name}.json',dict(schema_version='moe-rdma-stream-work/2',streams=streams,work=work,
            limits=dict(max_streams=12,max_work=64)))
        work_truth[name]=details
    save(observed/'index.json',dict(config=cfg,artifacts={p.name:dict(sha256=sha(p),bytes=p.stat().st_size) for p in sorted(observed.iterdir())},
        scope='new packet windows and ECMP seeds, same four requests; Dispatch data only'))
    shutil.copyfile(args.manifest,truth/'manifest.json');shutil.copyfile(args.placements,truth/'placements.json')
    save(truth/'provenance.json',dict(manifest_sha256=sha(args.manifest),placements_sha256=sha(args.placements),
        selections=choices,work_source_mapping=work_truth,input_bytes=total,
        previously_aggregated_offline=True,independent_heldout_requests=False))
    print(json.dumps(dict(prepared=True,conditions=18,windows=4)))


if __name__=='__main__':main()
