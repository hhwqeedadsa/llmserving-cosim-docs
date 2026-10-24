"""Freeze one current native runtime and tiny explicit functional inputs."""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
from fixtures.context_transactions.stage import load,save,sha


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage','backend'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();assert not (s/'runtime').exists()
    runtime=s/'runtime';runtime.mkdir();(runtime/'lib').mkdir()
    shutil.copyfile(a.backend,runtime/'transaction-probe');(runtime/'transaction-probe').chmod(0o755)
    text=subprocess.check_output(['ldd',str(a.backend)],text=True);libraries={}
    for name,path in re.findall(r'(libns3[^ ]+) => (/[^ ]+)',text):
        src=Path(path);libraries[str(src)]=sha(src);shutil.copyfile(src,runtime/'lib'/name)
    assert len(libraries)==8
    save(s/'identity.json',dict(binary={str(a.backend.resolve()):sha(a.backend)},libraries=libraries,
        backend_sources={str(p.relative_to(repo)):sha(p) for p in sorted((repo/'backend/rdma').glob('*')) if p.is_file()},
        native_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3',text=True).strip(),
        libraries_copied_without_build=True))
    cfg=load(repo/'examples/scaleout_rdma_transactions/nic-registration-topology.json');cfg['deployment']['gpu_type']='NONE';save(s/'topology-input.json',cfg)
    protocol=load(repo/'examples/scaleout_rdma/rdma.json');protocol.update(schema_version='moe-scaleout-rdma/2',payload_bytes=64,chunk_bytes=256,
        packet_capture=dict(nodes=[],start_ns=0,end_ns=1000000,max_events=20000,wire_headers=True),queue_metrics=True,
        limits=dict(max_gpus=8,max_switches=16,max_links=64,max_messages=16,max_queue_events=1,queue_capture='summary',
            max_sim_ns=1000000,wall_seconds=60,rss_bytes=2**30,address_bytes=2**31,artifact_bytes=64*2**20,result_json_bytes=16*2**20))
    save(s/'protocol.json',protocol)
    protocol['faults']=[dict(node=4,port=1,message_id=0,kind='data',sequence=0,start_ns=0,end_ns=1000000,count=1)]
    save(s/'fault-protocol.json',protocol)
    shutil.copyfile(repo/'examples/scaleout_context_transactions/program.json',s/'input.json')
    print('frozen unchanged transaction binary/eight ns-3 libraries and two-NIC tiny inputs')


if __name__=='__main__':main()
