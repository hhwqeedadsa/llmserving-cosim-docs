"""Prepare necessary-condition controls, or analyze only observed routing files."""
import argparse
from collections import Counter
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import random
import resource
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
SEEDS = [2026102201, 2026102202, 2026102203]


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, v): p.write_text(json.dumps(v, ensure_ascii=False, separators=(',', ':'))+'\n')


def load(folder):
    index = json.loads((folder/'index.json').read_text()); cache = {}; total = 0
    assert index['ranks'] == 4 and index['experts'] == 256 and index['top_k'] == 8
    assert index['decode_entries'] == 128 and index['source_layers'] == list(range(3,61))
    for name, info in index['artifacts'].items():
        assert Path(name).name == name
        p = folder/name; total += p.stat().st_size
        assert total <= 32*2**20 and p.stat().st_size == info['bytes'] and sha(p) == info['sha256']
        cache[name] = json.loads(p.read_text())
    return index, cache


def prepare(a):
    old, cache = load(a.old_observed)
    expected = json.loads((ROOT/'docs/review_artifacts/route_controls_20261022/summary.json').read_text())
    assert sha(a.old_observed/'index.json') == expected['observed_index_sha256']
    original = cache['routes-original.json']['selections']
    manifest = json.loads(a.manifest.read_text()); sources = []
    assert len(manifest['files']) == 4
    for src, item in enumerate(manifest['files']):
        p = ROOT/item['file']['path']; assert sha(p) == item['file']['sha256']
        raw = json.loads(p.read_text()); assert len(raw) == 129
        for li in range(58):
            assert [entry[str(li+3)][0] for entry in raw[1:]] == original[li][src]
        sources.append(dict(path=item['file']['path'],sha256=sha(p)))
    violations = sum(len({e//32 for e in row})>4 for layer in original for rows in layer for row in rows)
    assert violations == 0, 'Original violates assumed group cap; stop without generating controls'
    out = a.output; out.mkdir(); obs = out/'observed'; obs.mkdir(); truth = out/'truth'; truth.mkdir()
    shutil.copy2(a.manifest, truth/'manifest.json')
    save(truth/'provenance.json', dict(sources=sources, old_index_sha256=sha(a.old_observed/'index.json'),
        manifest_sha256=sha(a.manifest), original_cap_violations=violations, seeds=SEEDS,
        attempts_per_cell=4096, model_revision=manifest['source']['collector_model_revision'],
        schema_version='moe-routing-constraint-provenance/1'))
    artifacts = {}; conditions = []; logs = []
    def artifact(name, value):
        save(obs/name,value); artifacts[name]=dict(sha256=sha(obs/name),bytes=(obs/name).stat().st_size)
        return name
    placement = artifact('placement-contiguous.json', cache['placement-contiguous.json'])
    artifact('routes-original.json',cache['routes-original.json'])
    for mode in ('cap4','within32'):
        for seed in SEEDS:
            rng = random.Random(seed); tensor = deepcopy(original); cells = []
            for li, layer in enumerate(tensor):
                for src, rows in enumerate(layer):
                    sets = list(map(set,rows)); rejected = Counter(); accepted = 0
                    for _ in range(4096):
                        i,j,p,q = rng.randrange(128),rng.randrange(128),rng.randrange(8),rng.randrange(8)
                        x,y = rows[i][p],rows[j][q]
                        if i==j or x==y or y in sets[i] or x in sets[j]: rejected['basic']+=1; continue
                        if mode=='within32' and x//32 != y//32: rejected['different_group']+=1; continue
                        ni = (sets[i]-{x})|{y}; nj = (sets[j]-{y})|{x}
                        if mode=='cap4' and (len({e//32 for e in ni})>4 or len({e//32 for e in nj})>4):
                            rejected['cap']+=1; continue
                        sets[i],sets[j]=ni,nj; rows[i][p],rows[j][q]=y,x; accepted+=1
                    cells.append(dict(layer=li+3,source=src,attempts=4096,accepted=accepted,rejected=dict(rejected)))
                    assert sum(rejected.values())+accepted == 4096
            name=mode+'_'+str(seed)
            routes=artifact('routes-'+name+'.json',dict(schema_version='moe-route-control-tensor/1',selections=tensor))
            conditions.append(dict(id=name,routes=routes,placement=placement))
            logs.append(dict(condition=name,cells=cells))
    save(obs/'index.json',dict(schema_version='moe-routing-constraints-observed/1',ranks=4,experts=256,top_k=8,
        decode_entries=128,source_layers=list(range(3,61)),artifacts=artifacts,conditions=conditions,
        scope='Necessary group-cap consistency only; not model-executable or score-valid routes'))
    save(truth/'proposals.json',logs)
    print(json.dumps(dict(conditions=6,original_cap_violations=0,attempts=6*232*4096)))


def analyze(a):
    old, oc = load(a.old_observed); new, nc = load(a.new_observed)
    baseline=oc['routes-original.json']['selections']
    assert baseline == nc['routes-original.json']['selections']
    a.output.mkdir(); results=[]
    with (a.output/'windows.csv').open('w',newline='') as f:
        columns='condition layer entry matrix_bits copies compute_max'.split()
        writer=csv.DictWriter(f,fieldnames=columns); writer.writeheader()
        for index, cache in ((old,oc),(new,nc)):
            for c in index['conditions']:
                routes=cache[c['routes']]['selections']; owners=cache[c['placement']]['owners']
                hist=Counter(); changed=cap_bad=copies=remote=matrix_changed=group_count_changed=0
                digest=hashlib.sha256(); aggregate=[0]*16
                for li,layer in enumerate(routes):
                    for step in range(128):
                        matrix=[0]*16; bm=[0]*16; compute=[0]*4
                        for src in range(4):
                            row=layer[src][step]; base=baseline[li][src][step]
                            assert len(row)==len(set(row))==8 and all(type(e) is int and 0<=e<256 for e in row)
                            ng=len({e//32 for e in row}); hist[ng]+=1; cap_bad+=ng>4
                            changed+=set(row)!=set(base)
                            group_count_changed+=Counter(e//32 for e in row)!=Counter(e//32 for e in base)
                            for e in row:
                                dst=owners[li][e]; compute[dst]+=1; remote+=dst!=src
                            for dst in {owners[li][e] for e in row}-{src}:matrix[src*4+dst]=1
                            for dst in {owners[li][e] for e in base}-{src}:bm[src*4+dst]=1
                        bits=''.join(map(str,matrix)); digest.update(bits.encode()); copies+=sum(matrix)
                        aggregate=[x+y for x,y in zip(aggregate,matrix)]; matrix_changed+=matrix!=bm
                        writer.writerow(dict(condition=c['id'],layer=li+3,entry=step+1,matrix_bits=bits,
                            copies=sum(matrix),compute_max=max(compute)))
                results.append(dict(condition=c['id'],token_selections=29696,windows=7424,
                    logical_group_histogram=dict(sorted(hist.items())),cap_violations=cap_bad,
                    changed_token_sets=changed,changed_token_group_counts=group_count_changed,
                    changed_matrices_same_placement=matrix_changed,direct_copies=copies,
                    expert_remote_contributions=remote,aggregate_matrix=aggregate,matrix_sha256=digest.hexdigest()))
    save(a.output/'summary.json',dict(schema_version='moe-routing-constraints-analysis/1',conditions=results,
        observed_indices_sha256=[sha(a.old_observed/'index.json'),sha(a.new_observed/'index.json')],
        reads_truth_or_raw_sources=False,network_executed=False,model_executable_claimed=False,
        scope='Four recorded Decode requests; declared EP4/contiguous64 for new controls; logical32 group cap4'))
    print(json.dumps({r['condition']:dict(cap_violations=r['cap_violations'],copies=r['direct_copies']) for r in results}))


def main():
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare'); p.add_argument('--manifest',type=Path,required=True)
    q=sub.add_parser('analyze'); q.add_argument('--new-observed',type=Path,required=True)
    for p in (p,q):
        p.add_argument('--old-observed',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    a=parser.parse_args(); start=time.monotonic()
    resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30));resource.setrlimit(resource.RLIMIT_CPU,(60,60))
    assert not a.output.exists() and shutil.disk_usage(a.output.parent).free>=8*2**30+32*2**20
    (prepare if a.command=='prepare' else analyze)(a)
    assert sum(p.stat().st_size for p in a.output.rglob('*') if p.is_file())<=32*2**20
    save(a.output/'execution.json',dict(argv=sys.argv,cwd=str(Path.cwd()),script_sha256=sha(Path(__file__)),
        completed=True,wall_seconds=time.monotonic()-start,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))


if __name__=='__main__':main()
