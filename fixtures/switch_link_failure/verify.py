"""Audit saved public examples and ensure material corruptions are rejected."""
import argparse
from copy import deepcopy
import importlib.util
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text())


def main(stage, examples, output):
    spec = importlib.util.spec_from_file_location('link_audit', Path(__file__).with_name('audit.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    topology = load(stage/'queued-topology/topology.json')
    audits = {}
    for name, config, messages in [('queued-failover', 'queued-rdma.json', 'messages.json'),
                                   ('two-failures', 'two-failures-rdma.json', 'messages.json'),
                                   ('pfc-valid-buffer', 'pfc-rdma.json', 'pfc-messages.json')]:
        audits[name] = module.audit(topology, load(examples/config), load(examples/messages), load(stage/name/'result.json'))
    original = load(stage/'queued-failover/result.json')
    rejected = {}
    for mutation in ('lost_physical_drop', 'lost_local_drop', 'wrong_mmu', 'early_route_update',
                     'fake_ack', 'wrong_queue_integral', 'missing_final_snapshot', 'post_close_tx'):
        candidate = deepcopy(original)
        if mutation.startswith('lost_'):
            kind = 2 if mutation == 'lost_physical_drop' else 5
            capture = candidate['packet_capture']
            index = next(i for i, e in enumerate(capture['events']) if e[3] == kind)
            del capture['events'][index]
            del capture['wire_headers']['event_header_indices'][index]
            capture['observations'] -= 1
            if kind == 5:
                candidate['link_failures']['local_drops'] -= 1
        elif mutation == 'wrong_mmu':
            candidate['link_failures']['events'][0]['buffer_after'][0]['switch_ingress_and_headroom_bytes'] += 1036
        elif mutation == 'early_route_update':
            candidate['link_failures']['events'][1]['time_ns'] -= 1
        elif mutation == 'fake_ack':
            candidate['messages'][0]['ack_ns'] -= 1000
        elif mutation == 'wrong_queue_integral':
            next(p for p in candidate['ports'] if p['node'] == 8)['queue_metrics']['area_byte_ns'] += 1
        elif mutation == 'missing_final_snapshot':
            candidate['mmu_snapshot']['selected_nodes'].pop()
        elif mutation == 'post_close_tx':
            e = next(e for e in candidate['packet_capture']['events'] if e[1:4] == [8, 3, 0])
            e[0] = 4000
        try:
            module.audit(topology, load(examples/'queued-rdma.json'), load(examples/'messages.json'), candidate)
        except ValueError as error:
            rejected[mutation] = str(error)
        else:
            raise ValueError('auditor accepted '+mutation)
    output.mkdir(exist_ok=False)
    (output/'audits.json').write_text(json.dumps(audits, indent=2, sort_keys=True)+'\n')
    (output/'tampering.json').write_text(json.dumps(rejected, indent=2, sort_keys=True)+'\n')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('stage', 'examples', 'output'):
        p.add_argument('--'+name, required=True, type=Path)
    args = p.parse_args()
    main(args.stage, args.examples, args.output)
