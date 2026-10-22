"""Bounded structural controls of four recorded Decode routing sequences."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import resource
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, value): p.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':'))+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    a = parser.parse_args(); start = time.monotonic(); out = a.output.resolve()
    resource.setrlimit(resource.RLIMIT_AS, (2*2**30, 2*2**30))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    assert not out.exists() and out.parent.is_dir() and shutil.disk_usage(out.parent).free >= 8*2**30+16*2**20
    assert a.config.stat().st_size <= 16384 and a.manifest.stat().st_size <= 2**20
    cfg = json.loads(a.config.read_text()); manifest = json.loads(a.manifest.read_text())
    assert cfg['schema_version'] == 'moe-route-controls/1'
    assert (cfg['ranks'],cfg['experts'],cfg['top_k'],cfg['decode_entries'],cfg['source_layers']) == (4,256,8,128,[3,60])
    assert cfg['dataset_revision'] == manifest['source']['dataset_revision'] == '27febb7b2d24169560a9f6f83d38eb53f94916cf'
    assert cfg['phase_offsets'] == [0,17,43,79] and cfg['swap_attempts_per_cell'] == 4096
    assert all(len(cfg[k]) == len(set(cfg[k])) == 3 and all(type(s) is int and 0 <= s < 2**32 for s in cfg[k])
               for k in ('swap_seeds','placement_seeds'))
    assert len(manifest['files']) == 4 and manifest['model']['hidden_dim'] == 7168
    assert len(manifest['layers']) == 61 and all(l['num_experts']==256 and l['top_k']==8 for l in manifest['layers'][3:])
    original = [[None]*4 for _ in range(58)]; inputs = []; total = 0
    for source, item in enumerate(manifest['files']):
        path = a.source_root.resolve()/item['file']['path']; total += path.stat().st_size
        assert total <= 16*2**20 and sha(path) == item['file']['sha256']
        raw = json.loads(path.read_text()); assert len(raw) == 129
        for li, layer in enumerate(range(3,61)):
            rows = []
            for entry in raw[1:]:
                tokens = entry[str(layer)]
                assert len(tokens)==1 and len(tokens[0])==len(set(tokens[0]))==8
                assert all(type(e) is int and 0 <= e < 256 for e in tokens[0])
                rows.append(tokens[0])
            original[li][source] = rows
        inputs.append(deepcopy(item))
        del raw
    out.mkdir(); observed = out/'observed'; observed.mkdir(); truth = out/'truth'; truth.mkdir()
    shutil.copy2(a.config, truth/'config.json'); shutil.copy2(a.manifest, truth/'manifest.json')
    artifacts = {}; conditions = []

    def artifact(name, schema, value):
        path = observed/name; save(path, dict(schema_version=schema, **value))
        artifacts[name] = dict(sha256=sha(path), bytes=path.stat().st_size)
        return name

    def routes(name, selections):
        return artifact(name, 'moe-route-control-tensor/1', dict(selections=selections))

    contiguous = artifact('placement-contiguous.json', 'moe-route-control-placement/1',
                          dict(owners=[[e//64 for e in range(256)] for _ in range(58)]))
    original_file = routes('routes-original.json', original)
    conditions.append(dict(id='original', routes=original_file, placement=contiguous))
    shifted = [[rows[shift:]+rows[:shift] for rows,shift in zip(layer,cfg['phase_offsets'])] for layer in original]
    conditions.append(dict(id='phase_offset', routes=routes('routes-phase.json',shifted), placement=contiguous))
    logs = []
    for seed in cfg['swap_seeds']:
        rng = random.Random(seed); swapped = deepcopy(original); cells = []
        for li, layer in enumerate(swapped):
            for src, rows in enumerate(layer):
                sets = list(map(set,rows)); accepted = 0
                for _ in range(cfg['swap_attempts_per_cell']):
                    i,j,p,q = rng.randrange(128),rng.randrange(128),rng.randrange(8),rng.randrange(8)
                    ei,ej = rows[i][p],rows[j][q]
                    if i==j or ei==ej or ej in sets[i] or ei in sets[j]: continue
                    sets[i].remove(ei);sets[i].add(ej);sets[j].remove(ej);sets[j].add(ei)
                    rows[i][p],rows[j][q] = ej,ei; accepted += 1
                assert Counter(e for row in rows for e in row) == Counter(e for row in original[li][src] for e in row)
                assert all(len(row)==len(set(row))==8 for row in rows)
                cells.append(dict(source_layer=li+3, source_rank=src, attempts=4096, accepted=accepted,
                                  changed_token_sets=sum(set(a)!=set(b) for a,b in zip(rows,original[li][src]))))
                assert time.monotonic()-start < 55
        name = 'swap_'+str(seed)
        conditions.append(dict(id=name, routes=routes('routes-'+name+'.json',swapped), placement=contiguous))
        logs.append(dict(seed=seed, cells=cells, uniform_sampling_claimed=False, mixing_proved=False))
    for seed in cfg['placement_seeds']:
        rng = random.Random(seed); owners = []
        for _ in range(58):
            shuffled = list(range(256)); rng.shuffle(shuffled); mapping = [None]*256
            for position,expert in enumerate(shuffled): mapping[expert] = position//64
            assert Counter(mapping) == Counter({0:64,1:64,2:64,3:64}); owners.append(mapping)
        name = 'placement_'+str(seed)
        placement = artifact(name+'.json', 'moe-route-control-placement/1', dict(owners=owners))
        conditions.append(dict(id=name,routes=original_file,placement=placement))
    save(observed/'index.json', dict(schema_version='moe-route-controls-observed/1', ranks=4,experts=256,top_k=8,
         decode_entries=128, source_layers=list(range(3,61)), artifacts=artifacts, conditions=conditions,
         scope='Offline conditional Dispatch operands; constructed alignment/controls, not actual launches or executable inference routes.'))
    save(truth/'swap_summary.json', logs)
    save(truth/'provenance.json', dict(manifest_sha256=sha(a.manifest),config_sha256=sha(a.config),files=inputs,
         source=manifest['source'],prompt_used=False,model_inference_executed=False,
         source_rank_assignment='manifest file order',ranks=4,batch_per_rank=1,
         synchronized_windows_observed=False,selection_precision='AWQ routing observation; transport dtype not measured',
         python_version=sys.version,script_sha256=sha(Path(__file__))))
    assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file()) <= 16*2**20
    save(out/'execution.json',dict(argv=sys.argv,cwd=str(Path.cwd()),completed=True,wall_seconds=time.monotonic()-start,
         peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,index_sha256=sha(observed/'index.json')))
    print(json.dumps(dict(completed=True,conditions=len(conditions),route_tensors=5,windows_per_condition=7424,
                         swap_attempts=4096*232*3,network_executed=False)))


if __name__ == '__main__': main()
