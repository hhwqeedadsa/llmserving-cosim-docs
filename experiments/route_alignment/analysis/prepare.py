"""Prepare declared inputs and predictions; no network execution."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess

from experiments.route_alignment.analysis.common import (NAMES, NETWORKS, ALIGNMENTS,
    PAYLOAD, program, predict, save, load, sha, case_name)


def execute(repo, stage):
    assert not stage.exists(); stage.mkdir()
    observed = stage/'observed'; observed.mkdir(); raw_dir = observed/'routes'; raw_dir.mkdir()
    intake_path = repo/'experiments/route_coverage/results/local_additions_20261027/intake.json'
    intake = load(intake_path); save(observed/'intake.json',intake)
    rows = {r['name']:r for r in intake['files']}; routes = []; sources = []
    for name in NAMES:
        path = repo/'data/moe-trace-small'/(name+'.json'); row = rows[path.name]
        assert row['eligible_offline'] and row['group']=='new_user_supplied' and not row['duplicate_of']
        assert sha(path)==row['sha256']; shutil.copyfile(path,raw_dir/path.name)
        raw = load(path); selected = []
        for i in range(1,9):
            token, = raw[i]['3']; assert len(token)==8 and len(set(token))==8
            assert all(type(e) is int and 0<=e<256 for e in token); selected.append(token)
        routes.append(selected); sources.append(dict(name=path.name,sha256=row['sha256']))
    assert len({r['sha256'] for r in sources})==8
    save(observed/'selection.json',dict(names=NAMES,routes=routes,entry_indices=list(range(1,9)),layer=3))
    dcqcn = load(repo/'examples/scaleout_multirail_native/work.json')['dcqcn_reference']
    counters = []; invariant = None
    for alignment in ALIGNMENTS:
        for phase in range(8):
            work,meta = program(routes,alignment,phase,dcqcn)
            dest = observed/f'{alignment}-{phase}'; dest.mkdir()
            save(dest/'work.json',work); save(dest/'meta.json',meta)
            totals = Counter()
            for w in work['work']:
                if w['kind']=='write': totals[w['stream']] += w['bytes']
            if invariant is None: invariant=totals
            else: assert totals==invariant
            count = sum(w['kind']=='write' for w in work['work'])
            assert count*8*2*6<250000 and count*8*4+count*8*4<100000
            counters.append(dict(alignment=alignment,phase=phase,work=len(work['work']),writes=count,
                token_bytes=sum(totals.values()),source_data_packets=count*8,
                no_retry_data_ack_link_tx_ceiling=count*8*2*6,
                data_ack_endpoint_capture_ceiling=count*8*8))
    for name in NETWORKS:
        t = load(repo/'examples/scaleout_rdma/small-clos-100g.json')
        t['deployment'].update(servers_per_segment=4,gpus_per_server=1)
        t['nic']['rails_by_gpu_slot']=[[0]]
        if name=='core25':t['links']['agg_core']['rate_bps']=25000000000
        save(observed/(name+'-config.json'),t)
        p = load(repo/'examples/scaleout_rdma/rdma.json')
        p.update(schema_version='moe-scaleout-rdma/2',payload_bytes=PAYLOAD,
            switch_counter_storage='port_dense',queue_metrics=True,
            limits=dict(max_gpus=8,max_switches=16,max_links=256,max_messages=8192,
                max_queue_events=200000,queue_capture='summary',max_sim_ns=1000000,
                wall_seconds=30,rss_bytes=2**30,address_bytes=2*2**30,
                artifact_bytes=64*2**20,result_json_bytes=32*2**20),
            packet_capture=dict(nodes=list(range(8)),start_ns=0,end_ns=1000000,max_events=100000,wire_headers=True))
        p['ecn_by_rate']['25000000000']=deepcopy(next(iter(p['ecn_by_rate'].values())))
        save(observed/(name+'-protocol.json'),p)
    build_root = repo/'runs/switch_counter_storage_20261027'
    identity = load(build_root/'identity.json'); build = load(build_root/'build/backend.json')
    provenance = load(build_root/'build/provenance.json')
    assert identity['stream_binary_sha256']==sha(Path(build['stream_probe']))
    for name,digest in provenance['sources'].items(): assert sha(repo/name)==digest,name
    native = repo/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=native,text=True).strip()==identity['native_commit']
    assert not subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=native,text=True).strip()
    runtime = stage/'runtime';runtime.mkdir();(runtime/'lib').mkdir()
    binary = Path(build['stream_probe']);shutil.copyfile(binary,runtime/'stream-probe');(runtime/'stream-probe').chmod(0o755)
    for name,digest in identity['library_hashes'].items():
        source = build_root/'build/output/lib'/name;assert sha(source)==digest
        shutil.copyfile(source,runtime/'lib'/name)
    save(observed/'runtime-identity.json',dict(native_commit=identity['native_commit'],
         binary_sha256=identity['stream_binary_sha256'],library_hashes=identity['library_hashes'],
         backend_sources=provenance['sources'],build_platform_head=provenance['platform_head']))
    save(observed/'source.json',dict(files=sources,intake_sha256=sha(intake_path),source=intake['source'],
         remote_identity_verified=False,hardware_calibrated=False,
         projection='single-layer token-to-rank dispatch/combine bytes, not full serving or modern CCL'))
    save(stage/'preflight-counts.json',dict(conditions=counters,streams=56,marginals_identical=True))


def predictions(observed,output):
    output.mkdir()
    for network in NETWORKS:
        topology = load(observed/(network+'-topology/topology.json'))
        for alignment in ALIGNMENTS:
            for phase in range(8):
                root = observed/f'{alignment}-{phase}'
                save(output/(case_name(network,alignment,phase)+'.json'),predict(load(root/'work.json'),
                    load(root/'meta.json'),topology,load(observed/(network+'-protocol.json'))['seed']))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('prepare','predict'))
    p.add_argument('--repository',type=Path);p.add_argument('--stage',type=Path,required=True)
    a=p.parse_args()
    if a.action=='prepare':execute(a.repository.resolve(),a.stage.resolve())
    else:predictions(a.stage.resolve()/'observed',a.stage.resolve()/'predictions')
