"""Project a saved complete frontend group; retain every task and dependency."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil


def load(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x): p.write_text(json.dumps(x,indent=2)+'\n')


def prepare(s, original):
    out=s/'inputs/long';out.mkdir();(out/'originals').mkdir()
    rank_map={0:0,1:1,512:2,513:3};sources=[]
    for step in (0,1):
        path=original/f'graphs/g0.w{step}/wave.json'
        assert path.stat().st_size<=2*2**20
        source=load(path);assert source['rank_ids']==list(rank_map) and source['group_index']==0 and source['decode_step']==step
        assert {r['layer'] for r in source['routes']}==set(range(48))
        name=f'g0.w{step}.json';shutil.copy2(path,out/'originals'/name)
        w=deepcopy(source);w['rank_ids']=[rank_map[r] for r in w['rank_ids']]
        def message(m):
            m['src']=rank_map[m['src']];m['dst']=rank_map[m['dst']]
        for m in w['messages']:message(m)
        rows={}
        for rank,tasks in w['rank_nodes'].items():
            for t in tasks:
                if 'message' in t:message(t['message'])
            rows[str(rank_map[int(rank)])]=tasks
        w['rank_nodes']=rows
        for layer in w['routes']:
            for token in layer['tokens']:token['rank']=rank_map[token['rank']]
            for contribution in layer['contributions']:message(contribution)
        save(out/name,w)
        sources.append(dict(original_path=str(path),file='originals/'+name,bytes=path.stat().st_size,sha256=sha(path),
            remapped_file=name,remapped_sha256=sha(out/name)))
    save(out/'source.json',dict(rank_map=rank_map,sources=sources,source_kind='saved synthetic full Qwen3-30B-A3B frontend group, not measured routes',
        changed='physical deployment and persistent connection model; no original timing equivalence'))
    config=load(s/'inputs/selective-plan.json')
    config['waves']=['g0.w0.json','g0.w1.json'];config['streams']=[]
    for src in range(4):
        for dst in range(4):
            if src==dst:continue
            i=len(config['streams']);config['streams'].append(dict(id=i,src=src,dst=dst,plane=0,wire_port=22000+i,
                source_nic=1,destination_nic=1,queue_depth=8))
    config['limits'].update(max_streams=12,max_work=16384,max_link_transmissions=200000)
    config['transport_recovery'].update(inflight_bytes=36000,receive_buffer_bytes=65536)
    save(out/'plan.json',config)
    protocol=load(s/'inputs/normal-protocol.json');protocol.update(payload_bytes=9000,chunk_bytes=36000)
    protocol['limits']['max_messages']=16384;protocol['packet_capture']['nodes']=[0,1,2,3]
    save(out/'protocol.json',protocol)
    print(json.dumps(dict(passed=True,sources=sources)))


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);p.add_argument('--original',type=Path,required=True)
    a=p.parse_args();prepare(a.stage.resolve(),a.original.resolve())


if __name__=='__main__':main()
