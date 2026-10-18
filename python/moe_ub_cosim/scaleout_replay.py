"""Bounded, hash-checked per-request routes for the native RDMA frontend.

Only choices are replayed: compute and network timing still execute causally.
Relative source references use the repository root, as route_evidence does.
Snapshots use the run directory instead, making saved runs relocatable.
"""
from array import array
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .route_evidence import iter_sources, unique_object, validate_manifest
from .scaleout_topology import fields, integer, require


def read_ref(ref, base, maximum):
    fields(ref, 'path sha256', 'replay file reference')
    require(isinstance(ref['path'], str) and ref['path'], 'missing replay path')
    path = (Path(base)/ref['path']).resolve()
    require(path.is_file() and path.stat().st_size <= maximum, 'missing/oversized replay file '+str(path))
    raw = path.read_bytes()
    require(len(raw) <= maximum and hashlib.sha256(raw).hexdigest() == ref['sha256'], 'replay SHA-256 mismatch')
    return path, raw


class Replay:
    def __init__(self, config, base):
        self.config, self.base = config, Path(base)
        spec = config['route_replay']
        fields(spec, 'manifest layer_ids requests budget'+
               (' expert_to_group_rank' if 'expert_to_group_rank' in spec else ''), 'route replay')
        budget = spec['budget']
        fields(budget, 'max_files max_bytes', 'replay budget')
        integer(budget['max_files'], 'replay max files', 1, 4096)
        integer(budget['max_bytes'], 'replay input bytes', 1, 2147483648)
        self.manifest_path, self.manifest_raw = read_ref(spec['manifest'], self.base, 2097152)
        self.manifest = json.loads(self.manifest_raw, object_pairs_hook=unique_object)
        validate_manifest(self.manifest)
        m, w = config['model'], config['workload']
        source_model = self.manifest['model']
        require(m['name'] == source_model['name'] and m['hidden_size'] == source_model['hidden_dim'],
                'replay model name/hidden size mismatch; do not relabel model routes')
        self.layers = spec['layer_ids']
        require(isinstance(self.layers, list) and len(self.layers) == m['layers'] and self.layers and
                all(type(l) is int for l in self.layers) and
                self.layers == list(range(self.layers[0], self.layers[0]+len(self.layers))),
                'replay requires an explicit contiguous source layer range')
        if config['schema_version'] == 'moe-scaleout-service/2':
            require(self.layers == list(range(source_model['num_layers'])),
                    'full-model replay must include every original layer; use /1 for a finite MoE segment')
        for lid in self.layers:
            require(0 <= lid < len(self.manifest['layers']), 'replay source layer outside model')
            layer = self.manifest['layers'][lid]
            require(layer['kind'] == 'moe' and layer['num_experts'] == m['experts'] and
                    layer['top_k'] == m['top_k'], 'replay layer/E/Top-K mismatch')
        ranks = sorted(r for group in config['parallel']['groups'] for r in group)
        require(ranks == list(range(len(ranks))), 'replay rank set must be contiguous')
        self.group_size = len(config['parallel']['groups'][0])
        self.placement = spec.get('expert_to_group_rank')
        if self.placement is not None:
            require(all(len(g) == self.group_size for g in config['parallel']['groups']),
                    'explicit replay placement requires equal EP group sizes')
            require(isinstance(self.placement, list) and len(self.placement) == m['layers'], 'placement layer count mismatch')
            for row in self.placement:
                require(isinstance(row, list) and len(row) == m['experts'] and
                        all(type(i) is int and 0 <= i < self.group_size for i in row) and
                        Counter(row) == Counter({i:m['experts']//self.group_size for i in range(self.group_size)}),
                        'placement must keep an equal number of experts per GPU; replication unsupported')
        mapping, seen_slots = {}, set()
        require(isinstance(spec['requests'], list), 'invalid replay request map')
        for request in spec['requests']:
            fields(request, 'rank_id slot file_id first_decode_entry', 'replay request')
            rank, slot = request['rank_id'], request['slot']
            integer(rank, 'replay rank', 0, len(ranks)-1)
            integer(slot, 'replay slot', 0, w['batch_per_rank']-1)
            integer(request['first_decode_entry'], 'first decode entry', 1, 65536)
            require((rank, slot) not in seen_slots and isinstance(request['file_id'], str) and
                    request['file_id'] not in mapping, 'repeated request slot or source file')
            seen_slots.add((rank, slot)); mapping[request['file_id']] = request
        require(seen_slots == {(r,s) for r in ranks for s in range(w['batch_per_rank'])},
                'replay must explicitly supply every scheduled request; no cycling or cloning')
        require(len(mapping) <= budget['max_files'], 'replay file budget exceeded')
        selected = deepcopy(self.manifest)
        selected['files'] = [f for f in selected['files'] if f['file_id'] in mapping]
        require({f['file_id'] for f in selected['files']} == set(mapping), 'unknown replay file ID')
        total = 0
        for f in selected['files']:
            p = (self.base/f['file']['path']).resolve()
            require(p.is_file() and p.stat().st_size <= 16777216, 'missing/oversized raw route '+str(p))
            total += p.stat().st_size
            f['file']['path'] = str(p)
        require(total <= budget['max_bytes'], 'replay cumulative input budget exceeded')
        self.selected, self.input_bytes, self.records = selected, total, {}
        for file_id, source in iter_sources(selected):
            request = mapping[file_id]
            first = request['first_decode_entry']
            require(first+w['decode_steps']-1 <= source['available_decode_records'], 'source window exhausted; no EOF-to-EOS or route repetition')
            context = source['prompt_tokens']+first-1
            require(context <= w['context_tokens'], 'source context exceeds declared context upper bound')
            choices = array('I')
            for entry in range(first, first+w['decode_steps']):
                for lid in self.layers:
                    choices.extend(source['raw'][entry][str(lid)][0])
            identity = request['rank_id']*w['batch_per_rank']+request['slot']
            self.records[identity] = dict(**request, context_tokens=context, choices=choices,
                                         source_file=source['file'], original_path=source['original_path'])

    def contexts(self, rank):
        batch = self.config['workload']['batch_per_rank']
        return [self.records[rank*batch+s]['context_tokens'] for s in range(batch)]

    def routes(self, group, step, layer):
        m, w = self.config['model'], self.config['workload']
        integer(step, 'replay Decode step', 0, w['decode_steps']-1)
        integer(layer, 'replay layer', 0, m['layers']-1)
        begin = (step*m['layers']+layer)*m['top_k']
        result = []
        for rank in group:
            for slot in range(w['batch_per_rank']):
                identity = rank*w['batch_per_rank']+slot
                row = self.records[identity]
                result.append(dict(rank=rank, slot=slot, experts=list(row['choices'][begin:begin+m['top_k']]),
                    source=dict(request_id=identity, file_id=row['file_id'],
                                source_entry=row['first_decode_entry']+step, source_token_row=0,
                                source_layer_id=self.layers[layer], sha256=row['source_file']['sha256'])))
        return result

    def owner(self, group, layer, expert):
        index = self.placement[layer][expert] if self.placement is not None else expert//(self.config['model']['experts']//len(group))
        return group[index]

    def snapshot(self, directory):
        """Copy only selected originals; use run-relative refs without changing IDs."""
        directory = Path(directory); target = directory/'routes'; target.mkdir()
        manifest = deepcopy(self.selected)
        for i, f in enumerate(manifest['files']):
            _, raw = read_ref(f['file'], self.base, 16777216)
            name = f'routes/source-{i:04d}.json'
            (directory/name).write_bytes(raw)
            f['file']['path'] = name
        encoded = (json.dumps(manifest, indent=2)+'\n').encode()
        (target/'manifest.json').write_bytes(encoded)
        c = deepcopy(self.config)
        c['route_replay']['manifest'] = dict(path='routes/manifest.json',sha256=hashlib.sha256(encoded).hexdigest())
        provenance = dict(source=self.manifest['source'], original_manifest=self.config['route_replay']['manifest'],
                          input_bytes=self.input_bytes, requests=len(self.records),
                          source_layer_ids=self.layers, scope='original_choices_declared_compute_and_deployment',
                          eos_inferred=False, raw_snapshots=len(manifest['files']))
        return c, provenance
