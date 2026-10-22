"""Predeclared short real-route feedback sensitivity study, via public CLI only."""
import argparse
from collections import Counter
from copy import deepcopy
from fractions import Fraction
import hashlib
from itertools import product
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from prefill_pressure import project

ROOT = Path(__file__).resolve().parents[2]
FACTORS = ('depth', 'np_ns', 'recovery_ns', 'cnp_bytes')
LEVELS = ((1, 8), (5000, 50000), (5000, 55000), (48, 512))


def load(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(args):
    out = args.output.resolve()
    assert not out.exists()
    manifest = load(args.manifest)
    assert manifest['source']['dataset_revision'] == '27febb7b2d24169560a9f6f83d38eb53f94916cf'
    assert len(manifest['files']) == 4
    raw = []
    for item in manifest['files']:
        path = ROOT/item['file']['path']
        assert path.stat().st_size < 4*2**20 and sha(path) == item['file']['sha256']
        raw.append(load(path))
    assert [len(r[0]['4']) for r in raw] == [105, 28, 10, 6]
    out.mkdir()
    observed, truth = out/'observed', out/'truth'
    observed.mkdir()
    truth.mkdir()
    shutil.copyfile(args.manifest, truth/'manifest.json')
    refs, work = [], []
    for src, request in enumerate(raw):
        for token, choices in enumerate(request[0]['4'][:6]):
            assert len(set(choices)) == len(choices) == 8
            assert all(type(e) is int and 0 <= e < 256 for e in choices)
            for dst in sorted({e//64 for e in choices} - {src}):
                identity = len(work)
                work.append(dict(id=identity, stream=4*src+dst, bytes=14464, after=[]))
                refs.append(dict(id=identity, source_rank=src, token=token, destination_rank=dst))
    save(truth/'provenance.json', dict(layer=4, entry=0, tokens_per_request=6,
        input_expert_placement='expert_id//64', source_assignment='manifest order', work=refs,
        independent_heldout=False, arrival_process_observed=False))
    topology = load(ROOT/'examples/scaleout_rdma/small-clos-100g.json')
    topology['deployment']['gpu_type'] = 'NONE'
    for tier in ('tor_agg', 'agg_core', 'tor_core'):
        topology['links'][tier]['rate_bps'] = 25000000000
    save(observed/'topology.json', topology)
    protocol = load(ROOT/'examples/scaleout_rdma/rdma.json')
    protocol.update(schema_version='moe-scaleout-rdma/2', queue_metrics=True,
        packet_capture=dict(nodes=[0, 2, 4, 6], start_ns=0, end_ns=5000000, max_events=100000),
        limits=dict(max_gpus=32, max_switches=16, max_links=256, max_messages=8192,
            max_queue_events=200000, queue_capture='summary', max_sim_ns=5000000,
            wall_seconds=60, rss_bytes=2**30, address_bytes=2**31,
            artifact_bytes=64*2**20, result_json_bytes=16*2**20))
    protocol['ecn_by_rate']['25000000000'] = deepcopy(protocol['ecn_by_rate']['100000000000'])
    save(observed/'protocol.json', protocol)
    cases = []
    for depth, np_ns, recovery_ns, size in product(*LEVELS):
        name = f'd{depth}-n{np_ns}-r{recovery_ns}-b{size}'
        config = dict(mode='figure7_reference/1', np_interval_ns=np_ns, alpha_interval_ns=55000,
            recovery_interval_ns=recovery_ns, recovery_bytes=10000000,
            min_rate_bps=100000000, additive_bps=5000000, hyper_bps=50000000,
            fast_recovery_rounds=5, gain=1/256, cnp_model_bytes=size, max_events=10000)
        streams = [dict(id=4*s+d, src=2*s, dst=2*d, plane=0, wire_port=21000+4*s+d,
            source_nic=1, destination_nic=1, queue_depth=depth)
            for s in range(4) for d in range(4) if s != d]
        save(observed/(name+'.json'), dict(schema_version='moe-rdma-stream-work/2',
            limits=dict(max_streams=12, max_work=72), streams=streams, work=work,
            dcqcn_reference=config,
            source_rate_capture=dict(start_ns=0, end_ns=5000000, max_events=10000)))
        cases.append(dict(name=name, depth=depth, np_ns=np_ns, recovery_ns=recovery_ns, cnp_bytes=size))
    save(observed/'index.json', dict(schema_version='moe-feedback-factorial/1', cases=cases,
        work_count=len(work), source_token_counts=[6]*4, layer=4, token_bytes=14464,
        artifacts={p.name:dict(sha256=sha(p), bytes=p.stat().st_size) for p in sorted(observed.iterdir())},
        hardware_calibrated=False, full_inference_modeled=False, independent_heldout=False))
    print(json.dumps(dict(prepared=True, conditions=16, work_count=len(work))))


def run(args):
    observed, backend, out = (p.resolve() for p in (args.observed, args.backend, args.output))
    assert not out.exists() and backend.is_file()
    index = load(observed/'index.json')
    for name, identity in index['artifacts'].items():
        assert sha(observed/name) == identity['sha256']
    out.mkdir()
    commands = []
    backend_sha = sha(backend)
    def cli(label, tail):
        assert sha(backend) == backend_sha
        command = [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', *map(str, tail)]
        row = dict(label=label, command=command, cwd=str(ROOT))
        commands.append(row)
        start = time.monotonic()
        try:
            with (out/(label+'.stdout.log')).open('w') as stdout, (out/(label+'.stderr.log')).open('w') as stderr:
                child = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr, timeout=60)
            row['exit_code'] = child.returncode
            assert child.returncode == 0, 'Public CLI failed: preserve prefix and stop; never retry'
        finally:
            row['wall_seconds'] = time.monotonic()-start
            save(out/'commands.json', commands)
    cli('topology', ['topology', '--config', observed/'topology.json', '--output', out/'topology'])
    for case in index['cases']:
        name = case['name']
        cli(name, ['rdma-stream-probe', '--topology', out/'topology', '--protocol', observed/'protocol.json',
            '--config', observed/(name+'.json'), '--backend', backend, '--output', out/name])
        result = load(out/name/'result.json')
        assert result['packet_capture']['complete'] and result['drained'] and result['passed']
        assert result['fault_drops'] == 0
        # This study does not have an independently validated retransmission baseline.
        for stream in result['persistent_transport']['streams']:
            assert stream['source_payload_bytes'] == stream['submitted_bytes'], 'Retransmission: stop remaining conditions'
        assert all(p['pfc_pause'] == p['pfc_resume'] == 0 for p in result['ports']), 'Unmodeled control in checker: stop'
    save(out/'execution.json', dict(completed=True, conditions=16, command_count=len(commands),
        backend_sha256=backend_sha, backend_path=str(backend), observed_index_sha256=sha(observed/'index.json'),
        platform_base_commit='591c62a', current_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        new_network_runs=16, hardware_calibrated=False, modern_GIN_executed=False))
    print(json.dumps(dict(completed=True, network_runs=16)))


def analyze(args):
    observed, runs, out = args.observed, args.run, args.output
    assert not out.exists()
    index = load(observed/'index.json')
    topology = load(runs/'topology/topology.json')
    rows = []
    for case in index['cases']:
        path = runs/case['name']/'result.json'
        r = load(path)
        t = r['persistent_transport']
        w = load(observed/(case['name']+'.json'))
        predicted, endpoints, _ = project(topology, w, 11)
        bound = max(Fraction((v['data']+v['ack'])*8*10**9,endpoints[k][1]) for k,v in predicted.items())
        control_excess = 0
        for p in r['ports']:
            v = predicted[p['node'], p['port']]
            assert p['data_tx_bytes'] == v['data'] and p['data_packets'] == v['data_packets']
            assert p['control_tx_bytes'] >= v['ack']
            control_excess += p['control_tx_bytes']-v['ack']
        columns = r['packet_capture']['columns']
        packets = [dict(zip(columns, e)) for e in r['packet_capture']['events']]
        kinds = Counter(e['kind'] for e in t['dcqcn_reference']['events'])
        ratecols = t['source_rate_capture']['columns']
        samples = [dict(zip(ratecols,s)) for s in t['source_rate_capture']['samples']]
        waits = [v['published_ns']-v['ready_ns'] for v in t['work']]
        acked = max(v['acked_ns'] for v in t['work'])
        assert bound <= acked
        rows.append(dict(**case, result_sha256=sha(path), work_count=len(t['work']),
            payload_bytes=sum(v['bytes'] for v in w['work']), source_packets=len(t['source_packets']),
            accepted_ns=max(v['accepted_ns'] for v in t['work']), acked_ns=acked, finish_ns=r['finish_ns'],
            feedback_tail_ns=r['finish_ns']-acked, minimum_port_service_ns=float(bound),
            ack_minus_service_ns=acked-float(bound),
            unique_destination_ce=sum(e['event']==1 and e['ip_protocol']==17 and e['ecn']==3 for e in packets),
            cnp_count=kinds['cnp_generated'], cnp_endpoint_bytes=kinds['cnp_generated']*case['cnp_bytes'],
            cnp_all_hop_bytes=control_excess, reference_event_counts=dict(kinds),
            min_rate_at_actual_tx_bps=min(s['rate_bps'] for s in samples if s['event']==0),
            min_rate_after_cnp_bps=min((s['rate_bps'] for s in samples if s['event']==4),default=None),
            peak_switch_queue_bytes=max(p.get('queue_metrics',{}).get('peak_bytes',0) for p in r['ports']),
            switch_queue_area_byte_ns=sum(p.get('queue_metrics',{}).get('area_byte_ns',0) for p in r['ports']),
            waiting_work=sum(v>0 for v in waits), max_publication_wait_ns=max(waits),
            sum_overlapping_publication_wait_ns=sum(waits)))
    effects = []
    for factor, levels in zip(FACTORS, LEVELS):
        for low in [r for r in rows if r[factor]==levels[0]]:
            high = next(r for r in rows if r[factor]==levels[1] and all(r[k]==low[k] for k in FACTORS if k!=factor))
            effects.append(dict(factor=factor, low_case=low['name'], high_case=high['name'],
                ack_delta_ns=high['acked_ns']-low['acked_ns'],
                ack_delta_percent=100*(high['acked_ns']/low['acked_ns']-1),
                cnp_delta=high['cnp_count']-low['cnp_count'],
                finish_delta_ns=high['finish_ns']-low['finish_ns']))
    out.mkdir()
    save(out/'summary.json', dict(cases=rows, paired_effects=effects, conditions=len(rows),
        analysis_reads_truth=False, statistical_independence_claimed=False,
        service_is_lower_bound_not_latency_prediction=True, hardware_calibrated=False,
        scope='Known six-token prefixes of four requests, Dispatch data only, shared native RDMA per condition'))
    print(json.dumps(dict(analyzed=True, conditions=len(rows), paired_effects=len(effects))))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='stage', required=True)
    p=sub.add_parser('prepare')
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('run')
    for key in ('observed','backend','output'):p.add_argument('--'+key,type=Path,required=True)
    p=sub.add_parser('analyze')
    for key in ('observed','run','output'):p.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args()
    globals()[args.stage](args)


if __name__=='__main__':main()
