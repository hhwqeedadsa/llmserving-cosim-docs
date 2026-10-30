"""Prepare fifteen distinct real sources and freeze a direct BF16 128-card study."""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from common import ROOT, HERE, GROUPS, load, sha, save, need, originals, account
from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import make_wave, validate
from moe_ub_cosim.scaleout_topology import generate
from moe_ub_cosim.service_transport import message_preflight


def prepare(intake_path, routes, output):
    output.mkdir(parents=True, exist_ok=False)
    folder = output/'originals'
    folder.mkdir()
    intake = load(intake_path)
    selected = sorted((r for r in intake['files'] if r['eligible_offline']), key=lambda r:int(r['name'].split('.')[0]))
    need(len(selected) == len({r['sha256'] for r in selected}) == 15, 'expected all fifteen eligible originals')
    manifest = load(ROOT/'experiments/moe_trace_hotspots/results/four_requests_20261018/manifest.json')
    old_confirmations = {(r['file']['sha256'],r['original_path']) for r in manifest['files']}
    manifest['source'].update(kind='user_provided_model_routes', dataset_revision=None,
        source_url='https://huggingface.co/datasets/core12345/MoE_expert_selection_trace/tree/main/cognitivecomputations/DeepSeek-R1-AWQ/mmlu',
        usage_terms='User confirmed directory; local SHA-256 checked; remote revision/license not independently verified.')
    manifest['source']['unknown_fields'] = sorted(set(manifest['source']['unknown_fields'])|{'dataset_revision'})
    manifest['files'] = []
    requests, sources = [], []
    for i, row in enumerate(selected):
        name = row['name']
        need('/' not in name and '\\' not in name and (row['origin_directory_user_confirmed'] or
             (row['sha256'],row['original_path']) in old_confirmations), 'invalid/unconfirmed source')
        source, target = routes/name, folder/name
        need(sha(source) == row['sha256'], 'changed intake source '+name)
        target.write_bytes(source.read_bytes())
        gi, ordinal = i%2, i//2
        rank = GROUPS[gi][ordinal*64//(8 if gi == 0 else 7)]
        identity = 'local-'+name.split('.')[0]
        manifest['files'].append(dict(file_id=identity, original_path=row['original_path'],
            file=dict(path=str(target),sha256=sha(target)), format='token_layer_map_v1'))
        requests.append(dict(rank_id=rank,slot=0,file_id=identity,first_decode_entry=1))
        sources.append(dict(name=name,sha256=sha(target),rank=rank,group=gi,original_path=row['original_path']))
    save(output/'manifest.json',manifest)
    t = load(ROOT/'examples/scaleout_clos128/clos-128.json')
    t['intra_host']['enabled'] = False
    topology, report = generate(t)
    (output/'topology').mkdir()
    save(output/'topology-config.json',t)
    save(output/'topology/topology.json',topology)
    save(output/'topology/report.json',report)
    c = load(ROOT/'examples/astra_stream_service/service.json')
    c['model'].update(name='deepseek-ai/DeepSeek-R1',hidden_size=7168,ffn_hidden_size=2048,
                      experts=256,top_k=8,gpu_type='H100')
    c['workload'].update(batch_per_rank=1,context_tokens=2048)
    c['parallel']['groups'] = deepcopy(GROUPS)
    c['phase_completion'] = dict(mode='ep_transport_flush/1')
    c['token_transport'] = dict(mode='deepep_direct_bf16/1',combine='rank_reduced/1',local_reduce_ns_per_extra=100)
    c['route_replay'].update(request_layout='sparse_explicit/1',
        manifest=dict(path=str(output/'manifest.json'),sha256=sha(output/'manifest.json')),
        layer_ids=[3,4],requests=requests,budget=dict(max_files=15,max_bytes=64*2**20))
    replay = Replay(c, ROOT)
    waves = [make_wave(c,g,gi,step,1,replay) for gi,g in enumerate(GROUPS) for step in range(2)]
    files = originals(c,ROOT)
    accounting = [account(c,w,files) for w in waves]
    totals = {k:sum(r[k] for r in accounting) for k in accounting[0]}
    need(totals['layout_bytes'] == 12729024 and totals['expert_contributions'] == 480 and
         totals['remote_token_rank_units'] == 441, 'frozen route scope differs from accepted offline accounting')
    keys = sorted({(m['src'],m['dst'],m['plane']) for w in waves for m in w['messages']})
    transport = c['transport']
    transport['streams'] = [dict(id=i,src=s,dst=d,plane=p,wire_port=21000+i,
        source_nic=1,destination_nic=1,queue_depth=32) for i,(s,d,p) in enumerate(keys)]
    transport['limits'].update(max_streams=len(keys),max_work=2048,max_link_transmissions=200000)
    transport['transport_recovery'].update(inflight_bytes=2**20,receive_buffer_bytes=2**20+8999,max_events=30000)
    transport['dcqcn_reference']['max_events'] = 30000
    transport['source_rate_capture'] = dict(start_ns=0,end_ns=5000000,max_events=30000)
    protocol = load(ROOT/'examples/astra_stream_service/rdma.json')
    protocol.update(schema_version='moe-scaleout-rdma/2',payload_bytes=9000,chunk_bytes=9000,
        switch_counter_storage='port_dense',queue_metrics=True,
        switch_drop_capture=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=10000),
        limits=dict(max_gpus=128,max_switches=128,max_links=1024,max_messages=2048,
            max_queue_events=200000,queue_capture='summary',max_sim_ns=5000000,wall_seconds=60,
            rss_bytes=2**30,address_bytes=2**31,artifact_bytes=128*2**20,result_json_bytes=32*2**20))
    protocol['packet_capture']['max_events'] = 200000
    protocol['ecn_by_rate'] = {'400000000000':dict(kmin_bytes=32768,kmax_bytes=131072,pmax=1.0)}
    incoming, streams = Counter({r:0 for r in GROUPS[0]}), Counter()
    (output/'planned').mkdir()
    for wave in waves:
        for m in wave['messages']:
            streams[m['src'],m['dst'],m['plane']] += m['bytes']
            if wave['group_index'] == wave['decode_step'] == m['layer'] == 0 and m['phase'] == 'dispatch':
                incoming[m['dst']] += m['bytes']
        save(output/'planned'/f'{wave["graph_id"]}.json',wave)
    need(max(streams.values()) < 2**20, 'SR window might constrain planned work')
    hotspot = min(GROUPS[0],key=lambda rank:(-incoming[rank],rank))
    for case in ('normal','local'):
        p = deepcopy(protocol)
        if case == 'local':
            p['data_loss'] = dict(kind='periodic_destination_data/1',numerator=1,denominator=1,
                                  offset=0,start_ns=0,end_ns=100000,nodes=[hotspot])
        validate(c,topology,p,replay)
        preflight = message_preflight(topology,p,c)
        for wave in waves:
            preflight.accept_wave(wave,0)
        save(output/f'{case}-rdma.json',p)
    save(output/'service.json',c)
    summary = dict(passed=True,gpu_endpoints=128,ep_sizes=[64,64],independent_requests=15,
        requests_per_group=[8,7],experts_per_rank_per_layer=4,topology_counts=report['counts'],
        data_active_ranks=sorted({m[k] for w in waves for m in w['messages'] for k in ('src','dst')}),
        streams=len(keys),accounting=totals,hotspot_rank=hotspot,first_dispatch_incoming_bytes=dict(incoming),
        max_planned_stream_bytes=max(streams.values()),sources=sources,network_executed=False,
        intake_sha256=sha(intake_path),
        platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        method_hashes={p.name:sha(p) for p in HERE.iterdir() if p.suffix in ('.py','.md')},
        input_hashes={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()})
    save(output/'preparation.json',summary)
    print(json.dumps({k:summary[k] for k in ('passed','accounting','streams','hotspot_rank')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('intake','routes-root','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args = parser.parse_args()
    prepare(args.intake.resolve(),args.routes_root.resolve(),args.output.resolve())
