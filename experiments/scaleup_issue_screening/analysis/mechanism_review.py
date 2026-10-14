"""Reconcile C1 witnesses with sealed execution records and live-object settings.

This is an internal mechanism review, not an E3 measurement input. The archive
is read in order without restoring the saved graph or changing historical data.
"""
import argparse
from collections import Counter
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

from .workflow import ROOT, digest, host, read, save


TARGETS = ((7, 27, 70, 'aiv:8', 4295045029),
           (8, 58, 65, 'aiv:20', 4295086595))


def review(archive, witnesses, source_root, source_manifest, output):
    archive = Path(archive).resolve()
    inventory = read(str(archive)+'.json')
    if not inventory['verified'] or digest(archive) != inventory['archive_sha256']:
        raise ValueError('Original archive identity differs from its sealed inventory')
    witness = read(witnesses)
    selected = []
    for lid, consumer, source, core, tid in TARGETS:
        target = next(t for t in witness['targets'] if (t['layer_id'], t['rank_id']) == (lid, consumer))
        rows = [s for s in target['witness'] if s['rank_id'] == source and s['layer_id'] == lid]
        key = next(s for s in rows if s['transfer_id'] == tid and s['kind'] == 'SEND')
        expert = next(s for s in rows if s['name'] == 'expert-compute')
        start, end = expert['end_ns'], key['start_ns']
        steps = sorted([s for s in rows if start <= s['start_ns'] < end and s['end_ns'] <= end
                        and (s['name'] == 'combine-result-pack' or s['event'] in ('data_ready', 'local_read_complete'))],
                       key=lambda s: (s['start_ns'], s['end_ns']))
        assert steps and steps[0]['start_ns'] == start and steps[-1]['end_ns'] == end
        assert all(a['end_ns'] == b['start_ns'] for a, b in zip(steps, steps[1:]))
        assert all(s['resource_id'] == core for s in steps if s['name'] == 'combine-result-pack')
        selected.append(dict(layer_id=lid, consumer_rank=consumer, source_rank=source, core=core,
                             key_transfer_id=tid, start_ns=start, end_ns=end, steps=steps))
    needed_nodes = {(s['graph_id'], s['rank_id'], s['node_id']) for t in selected for s in t['steps']}
    needed_nodes.update((s['graph_id'], s['rank_id'], d['node_id'])
                        for t in selected for s in t['steps'] for d in s['latest_dependency_ties'])
    needed_transfers = {s['transfer_id'] for t in selected for s in t['steps'] if s['transfer_id'] is not None}
    needed_transfers.update(t['key_transfer_id'] for t in selected)
    names = {'run/raw/execution/effective_backend.json', 'run/raw/execution/result.json',
             'run/raw/execution/records/node_execution-g0.jsonl.gz',
             'run/raw/execution/records/transfers-g0.jsonl.gz',
             'run/raw/execution/records/planned_transfers-g0.jsonl.gz'}
    found = {}; nodes = {}; transfers = {}; planned = {}; effective = []; report = None
    with tarfile.open(archive, 'r|gz') as stream:
        for item in stream:
            if item.name not in names:
                continue
            assert item.isfile() and item.size <= 192*2**20
            data = stream.extractfile(item).read()
            identity = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            assert identity == inventory['entries'][item.name]
            found[item.name] = identity
            if item.name.endswith('.jsonl.gz'):
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as records:
                    for line in records:
                        assert len(line) <= 2**20
                        row = json.loads(line)
                        if 'node_execution' in item.name:
                            k = row['graph_id'], row['rank_id'], row['node_id']
                            if k in needed_nodes:
                                assert k not in nodes
                                nodes[k] = row
                        elif row['transfer_id'] in needed_transfers:
                            (planned if 'planned_transfers' in item.name else transfers)[row['transfer_id']] = row
            elif item.name.endswith('effective_backend.json'):
                value = json.loads(data)
                requested = value['attributes']
                effective = [r for r in value['objects']
                             if r['role'] in ('native_instance', 'native_api', 'native_thread:0')]
                for row in effective:
                    row['attributes'] = {k: v for k, v in row['attributes'].items() if k in requested}
                del value
            else:
                report = json.loads(data)
            del data
    assert set(found) == names and set(nodes) == needed_nodes
    assert set(transfers) == set(planned) == needed_transfers and report['normal_exit']
    rank_nodes = dict(zip(report['rank_ids'], report['rank_to_ub_node']))
    source_nodes = {rank_nodes[t['source_rank']] for t in selected}
    effective = [r for r in effective if r['node_id'] in source_nodes]
    assert len(effective) == 6
    expected = {'ns3::UbLdstInstance::ThreadNum': '1', 'ns3::UbLdstThread::StoreOutstanding': '32',
                'ns3::UbLdstThread::StoreRequestSize': '4096', 'ns3::UbLdstApi::RequestIdCount': '256'}
    for key, value in expected.items():
        assert requested[key] == value and any(r['attributes'].get(key) == value for r in effective)
    chains = []
    for target in selected:
        totals = Counter(); steps = []
        for step in target['steps']:
            actual = nodes[step['graph_id'], step['rank_id'], step['node_id']]
            assert (actual['start_ns'], actual['end_ns']) == (step['start_ns'], step['end_ns'])
            for predecessor in step['latest_dependency_ties']:
                assert nodes[step['graph_id'], step['rank_id'], predecessor['node_id']]['end_ns'] == predecessor['end_ns']
            kind = 'result_pack' if step['name'] == 'combine-result-pack' else step['event']
            if step['transfer_id'] is not None:
                t = transfers[step['transfer_id']]
                field = 'data_ready_ns' if kind == 'data_ready' else 'local_read_complete_ns'
                end = t[field] if field in t else t['local_facts'][field]
                assert end == step['end_ns'] and t['send_start_ns'] == step['start_ns']
                identity = {k: planned[step['transfer_id']][k] for k in ('operation_id','ub_task_id',
                    'src_rank','dst_rank','message_id','chunk_index','payload_bytes',
                    'business_payload_bytes','application_metadata_bytes','sources')}
            else:
                identity = None
            duration = step['end_ns']-step['start_ns']; totals[kind] += duration
            steps.append(dict(node_id=step['node_id'],kind=kind,start_ns=step['start_ns'],end_ns=step['end_ns'],
                              duration_ns=duration,transfer_id=step['transfer_id'],identity=identity,
                              latest_dependency_ties=step['latest_dependency_ties']))
        assert sum(totals.values()) == target['end_ns']-target['start_ns']
        chains.append({**{k:v for k,v in target.items() if k!='steps'},'duration_ns':sum(totals.values()),
                       'nonoverlapping_components_ns':dict(totals),'steps':steps})
    source_evidence = read(source_manifest); checked = []
    for entry in source_evidence['files']:
        path = Path(source_root)/entry['path']
        assert path.stat().st_size == entry['bytes'] and digest(path) == entry['sha256']
        checked.append(entry)
    save(output,dict(schema_version='issue-screening-c1-initial-review/1',passed=True,
        archive_sha256=inventory['archive_sha256'],archive_members=found,witness_sha256=digest(witnesses),
        chains=chains,requested_native_attributes={k:requested[k] for k in expected},effective_objects=effective,
        fixed_source=dict(repository=source_evidence['repository'],revision=source_evidence['revision'],
                          files_checked=checked,selected_call=source_evidence['selected_call']),
        limitations=['Witness predecessor ties retained; this is not unique-cause or avoidable-time attribution',
                     'Per-request queue state and resource release owners require passive supplemental capture'],
        simulation_executed=False,program_sha256=digest(__file__)))
    for chain in chains:
        print(chain['layer_id'],chain['duration_ns'],chain['nonoverlapping_components_ns'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('archive','witnesses','source-root','source-manifest','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args()
    if a.worker:
        review(a.archive,a.witnesses,a.source_root,a.source_manifest,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],
                str(a.output.resolve())+'-resources',**host(extra_roots=[a.output.parent]))
        if r['status']!='completed':raise SystemExit('Initial C1 review failed: '+r['status'])


if __name__=='__main__':main()
