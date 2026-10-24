"""Prepare independent finite topology/program inputs for capacity acceptance."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import runpy


def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();out=s/'inputs';out.mkdir()
    make=runpy.run_path(str(repo/'examples/scaleout_rdma_streams/ep_groups.py'))['program']
    source=repo/'examples/scaleout_rdma/small-clos-100g.json';template=json.loads(source.read_text())
    proto=json.loads((repo/'examples/scaleout_link_failure/stream-rdma.json').read_text());del proto['link_failures'];proto.pop('mmu_snapshot',None)
    del proto['ecn_by_rate']['25000000000'];proto['limits']['queue_capture']='summary'
    proto['packet_capture']['max_events']=100000
    for n in (8,128,1024):
        topology=deepcopy(template);h=topology['deployment'];h['gpu_type']='NONE'
        if n!=8:
            h.update(pods=4 if n==128 else 8,segments_per_pod=1,servers_per_segment=4 if n==128 else 16,gpus_per_server=8)
            topology['fabric']['tors_per_segment_per_rail']=2 if n==128 else 4
            topology['nic']['rails_by_gpu_slot']=[[0] for _ in range(8)]
        w=make(n,4,2,768,100);c=deepcopy(proto)
        c['packet_capture']['nodes']=list(range(n))
        c['limits'].update(max_gpus=n,max_switches=64,max_links=2048,max_messages=65536,
            rss_bytes=4*2**30,address_bytes=8*2**30,result_json_bytes=32*2**20)
        save(out/(str(n)+'-topology.json'),topology);save(out/(str(n)+'-work.json'),w);save(out/(str(n)+'-protocol.json'),c)
        if n==8:
            legacy=deepcopy(w);legacy['schema_version']='moe-rdma-stream-work/3';del legacy['limits']['max_link_transmissions']
            save(out/'legacy-work.json',legacy)
            c['limits'].update(max_switches=16,max_links=256,rss_bytes=2**30,address_bytes=2**31,result_json_bytes=16*2**20)
            save(out/'legacy-protocol.json',c)
    # All native compiles/links already completed; the first new unit test
    # exposed missing physical-NIC validation. Keep that failure, finalize the
    # build receipt only after the corrected Python regression passed.
    for receipt in s.glob('compile-*-monitor/probe.json'):
        x=json.loads(receipt.read_text());assert x['status']=='completed' and x['child_exit_code']==0
    for label in ('link-stream-probe','link-transaction-probe','link-transaction-session','python-corrected'):
        x=json.loads((s/(label+'-monitor/probe.json')).read_text());assert x['status']=='completed'
    libs={str(p):sha(p) for p in (s/'lib').glob('libns3*.so')};assert len(libs)==8
    fabric=Path('/dev/shm/moe-switch-link-failure-20261024/objects/libobserved-fabric.a');libs[str(fabric)]=sha(fabric)
    save(s/'build.json',dict(inputs=libs,binaries={p.name:sha(p) for p in (s/'bin').iterdir()},
        source_identity={str(p.relative_to(repo)):sha(p) for p in (repo/'backend/rdma').rglob('*') if p.suffix in ('.cc','.hh')},
        python_identity={str(p.relative_to(repo)):sha(p) for p in (repo/'python').rglob('*.py')},
        scope='all changed-layout consumers incrementally compiled; initial Python test failed, corrected source tests passed; no native rebuild'))


if __name__=='__main__':main()
