"""Prepare independent 128-endpoint Clos marker buffers with local DATA loss."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import make_wave, validate
from moe_ub_cosim.scaleout_topology import require

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source-manifest',type=Path,help='Optional R1 routing manifest; select first four distinct files')
    parser.add_argument('--source-base',type=Path,help='Required base for relative file references in a supplied manifest')
    args=parser.parse_args();out=args.output.resolve()
    require((args.source_manifest is None)==(args.source_base is None),'provide source manifest and base together')
    subprocess.run([sys.executable,'-B',str(ROOT/'examples/scaleout_inflight_helpers/prepare.py'),
                    '--output',str(out)],cwd=ROOT,check=True)
    c=json.loads((out/'service-helper.json').read_bytes())
    p=json.loads((out/'rdma-transient.json').read_bytes())
    if args.source_manifest:
        manifest=json.loads(args.source_manifest.read_bytes())
        require(manifest['model']['name']=='deepseek-ai/DeepSeek-R1' and
                manifest['model']['hidden_dim']==7168 and len(manifest['files'])>=4,
                'real example needs four distinct declared R1 files')
        manifest['files']=manifest['files'][:4]
        for row in manifest['files']:row['file']['path']=str((args.source_base/row['file']['path']).resolve())
        manifest_path=out/'buffer-source-manifest.json'
        with manifest_path.open('x') as stream:json.dump(manifest,stream,indent=2);stream.write('\n')
        c['model'].update(name='deepseek-ai/DeepSeek-R1',hidden_size=7168,ffn_hidden_size=18432)
        c['workload']['context_tokens']=65536
        c['parallel']['groups']=[list(range(i,128,4)) for i in range(4)]
        c['route_replay'].update(manifest=dict(path=str(manifest_path),sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest()),
            layer_ids=[3,4],budget=dict(max_files=4,max_bytes=16*2**20),requests=[
                dict(rank_id=i,slot=0,file_id=row['file_id'],first_decode_entry=1) for i,row in enumerate(manifest['files'])])
        c['source_ack_watchdog'].update(max_messages=512,max_notifications=512)
        replay=Replay(c,ROOT)
        waves=[make_wave(c,g,i,step,1,replay) for i,g in enumerate(c['parallel']['groups']) for step in range(2)]
        keys={(m['src'],m['dst'],m['plane']) for w in waves for m in w['messages']}
        keys.update((src,dst,row['plane']) for w in waves for row in w['helper_candidates']
                    for src,dst in [(row['source'],row['provider']),(row['provider'],row['source'])])
        c['transport']['streams']=[dict(id=i,src=s,dst=d,plane=plane,wire_port=21000+i,source_nic=1,
            destination_nic=1,queue_depth=32) for i,(s,d,plane) in enumerate(sorted(keys))]
        c['transport']['limits']['max_streams']=len(keys)
        incoming=Counter()
        for m in waves[0]['messages']:
            if m['phase']=='dispatch' and m['layer']==0:incoming[m['dst']]+=m['bytes']
        p['data_loss']['nodes']=[min(incoming,key=lambda rank:(-incoming[rank],rank))]
    c['transport']['message_buffers']=dict(schema_version='moe-service-message-buffers/1',
        content='deterministic_message_marker/1',capacity_bytes_per_gpu=2*2**20,
        max_retained_bytes=32*2**20,max_messages=4096,max_events=200000,
        max_payload_capture_bytes=32*2**20)
    p['limits'].update(wall_seconds=60,rss_bytes=2**30,address_bytes=2*2**30,
                       artifact_bytes=512*2**20,result_json_bytes=256*2**20,queue_capture='events')
    t=json.loads((out/'topology/topology.json').read_bytes())
    validate(c,t,p,Replay(c,ROOT))
    for name,value in [('service-buffers.json',c),('rdma-buffers.json',p)]:
        with (out/name).open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')
    print(json.dumps(dict(passed=True,network_executed=False,gpu_endpoints=128,
        synthetic_routes=args.source_manifest is None,synthetic_payload=True,
        mode='local fault with helper, original gates retained')))


if __name__=='__main__':main()
