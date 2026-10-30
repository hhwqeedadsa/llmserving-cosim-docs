"""Prepare a self-contained synthetic sparse-request Clos functional example.

No network is run. Four distinct synthetic originals test the public input path;
they are deliberately not labeled R1 measurements or independent real traces.
"""
import argparse
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import make_wave, validate
from moe_ub_cosim.scaleout_topology import generate
from moe_ub_cosim.service_transport import message_preflight

ROOT=Path(__file__).resolve().parents[2]


def save(path, value):
    with path.open('x') as stream:
        json.dump(value,stream,indent=2);stream.write('\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(gpus, output):
    if gpus not in (128,256,512):raise ValueError('supported Clos endpoint counts: 128,256,512')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    topology_config=json.loads((ROOT/'examples/scaleout_clos128/clos-128.json').read_bytes())
    topology_config['deployment']['pods']=gpus//64
    # Persistent service currently carries same-server traffic over RDMA too.
    topology_config['intra_host']['enabled']=False
    topology,counts=generate(topology_config)
    save(output/'topology-config.json',topology_config)
    folder=output/'topology';folder.mkdir();save(folder/'topology.json',topology);save(folder/'report.json',counts)
    manifest=json.loads((ROOT/'examples/scaleout_route_replay/manifest.json').read_bytes())
    name='synthetic/sparse-clos-H256-E256-K8'
    manifest['source'].update(dataset_id='repo/scaleout-sparse-clos',dataset_revision='example-v1',
                              collector_model_id=name,source_url='examples/scaleout_sparse_clos')
    manifest['model'].update(name=name,num_layers=2)
    manifest['layers']=[dict(layer_id=l,kind='moe',num_experts=256,top_k=8) for l in range(2)]
    manifest['files']=[]
    raw_folder=output/'sources';raw_folder.mkdir()
    for ordinal in range(4):
        raw=[{str(layer):[[(ordinal*41+token*17+entry*5+layer*7+j*29)%256 for j in range(8)]
                           for token in range(16+ordinal if entry==0 else 1)]
              for layer in range(2)} for entry in range(3)]
        path=raw_folder/f'{ordinal}.json';save(path,raw)
        manifest['files'].append(dict(file_id=f'synthetic-{ordinal}',original_path=f'synthetic/sparse-clos/{ordinal}.json',
            file=dict(path=str(path),sha256=sha(path)),format='token_layer_map_v1'))
    save(output/'manifest.json',manifest)
    c=json.loads((ROOT/'examples/astra_stream_service/service.json').read_bytes())
    c['model'].update(name=name,experts=256,top_k=8,gpu_type='H100')
    # Two slots per rank are available; only explicitly listed slots exist.
    c['workload']['batch_per_rank']=2
    groups=[list(range(begin,min(begin+256,gpus))) for begin in range(0,gpus,256)]
    c['parallel']['groups']=groups
    ranks=[ordinal*gpus//4 for ordinal in range(4)]
    c['route_replay'].update(request_layout='sparse_explicit/1',
        manifest=dict(path=str(output/'manifest.json'),sha256=sha(output/'manifest.json')),layer_ids=[0,1],
        requests=[dict(rank_id=rank,slot=ordinal%2,file_id=f'synthetic-{ordinal}',first_decode_entry=1)
                  for ordinal,rank in enumerate(ranks)])
    replay=Replay(c,ROOT)
    waves=[make_wave(c,group,index,step,1,replay) for index,group in enumerate(groups) for step in range(2)]
    keys=sorted({(m['src'],m['dst'],m['plane']) for wave in waves for m in wave['messages']})
    transport=c['transport']
    transport['streams']=[dict(id=i,src=s,dst=d,plane=p,wire_port=21000+i,
        source_nic=1,destination_nic=1,queue_depth=32) for i,(s,d,p) in enumerate(keys)]
    transport['limits'].update(max_streams=len(keys),max_work=512,max_link_transmissions=200000)
    transport['transport_recovery'].update(inflight_bytes=2**20,receive_buffer_bytes=2**20+8999,max_events=30000)
    transport['dcqcn_reference']['max_events']=30000
    p=json.loads((ROOT/'examples/astra_stream_service/rdma.json').read_bytes())
    p.update(schema_version='moe-scaleout-rdma/2',payload_bytes=9000,chunk_bytes=9000,switch_counter_storage='port_dense',
        limits=dict(max_gpus=gpus,max_switches=128,max_links=1024,max_messages=512,max_queue_events=200000,
            queue_capture='summary',max_sim_ns=5000000,wall_seconds=60,rss_bytes=2**30,address_bytes=2**31,
            artifact_bytes=128*2**20,result_json_bytes=32*2**20))
    p['packet_capture']['max_events']=200000
    p['ecn_by_rate']={'400000000000':dict(kmin_bytes=32768,kmax_bytes=131072,pmax=1.0)}
    validate(c,topology,p,replay)
    preflight=message_preflight(topology,p,c)
    for wave in waves:preflight.accept_wave(wave,0)
    save(output/'service.json',c);save(output/'rdma.json',p)
    summary=dict(passed=True,network_executed=False,gpu_endpoints=gpus,ep_sizes=list(map(len,groups)),
        source_ranks=ranks,independent_requests=len(replay.records),streams=len(keys),
        messages=sum(len(w['messages']) for w in waves),logical_bytes=sum(m['bytes'] for w in waves for m in w['messages']),
        synthetic=True,hardware_calibrated=False,scope='finite two-layer two-step functional input; not full-cluster load',
        counts=counts['counts'])
    save(output/'preparation.json',summary)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gpus',type=int,choices=(128,256,512),required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(prepare(args.gpus,args.output)))
