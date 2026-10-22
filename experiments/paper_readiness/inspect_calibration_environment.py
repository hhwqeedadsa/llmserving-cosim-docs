"""Bounded, read-only calibration readiness inspection. Never sends traffic.

Reads visible GPU topology, sysfs port state, verbs enumeration, tool help and
package metadata. Does not import torch, create a CUDA/RDMA context, install
software, enumerate credentials, enable ports or run any benchmark.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import resource
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
DEEP='f6aa38fe06cd4a0489e7730365d4e0114970a2f3'


def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True,help='New directory; raw local logs and sanitized summary')
    parser.add_argument('--python',action='append',default=[],help='Existing Python executable to inspect by metadata only; repeatable')
    args=parser.parse_args();out=args.output.resolve();start=time.monotonic()
    assert not out.exists() and out.parent.is_dir() and shutil.disk_usage(out.parent).free>=8*2**30+4*2**20
    assert len(args.python)<=4
    resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30));resource.setrlimit(resource.RLIMIT_CPU,(60,60))
    out.mkdir();commands=[]

    def run(argv):
        remaining=55-(time.monotonic()-start);assert remaining>0
        executable=shutil.which(argv[0]);index=len(commands)
        record=dict(argv=argv,timeout_seconds=min(10,remaining))
        stdout=stderr=''
        if executable is None:record.update(state='not_found_in_PATH',exitcode=None)
        else:
            with (out/f'{index}.stdout').open('w') as so,(out/f'{index}.stderr').open('w') as se:
                try:
                    result=subprocess.run([executable,*argv[1:]],stdout=so,stderr=se,
                                          timeout=record['timeout_seconds'],cwd=ROOT)
                    record.update(state='completed',exitcode=result.returncode)
                except subprocess.TimeoutExpired:record.update(state='timeout',exitcode=None)
            assert (out/f'{index}.stdout').stat().st_size+(out/f'{index}.stderr').stat().st_size<=256*2**10
            stdout=(out/f'{index}.stdout').read_text(errors='replace')
            stderr=(out/f'{index}.stderr').read_text(errors='replace')
            record.update(stdout=f'{index}.stdout',stderr=f'{index}.stderr')
        commands.append(record);save(out/'commands.json',commands)
        return record,stdout,stderr

    inventory,raw,_=run(['nvidia-smi','--query-gpu=index,name,compute_cap,memory.total,driver_version',
                         '--format=csv,noheader,nounits'])
    gpus=[]
    if inventory['exitcode']==0:
        for row in csv.reader(io.StringIO(raw)):
            assert len(row)==5
            index,name,cap,mib,driver=[s.strip() for s in row]
            gpus.append(dict(index=int(index),name=name,compute_capability=cap,
                memory_mib=int(mib),driver_version=driver))
    topology,raw,_=run(['nvidia-smi','topo','-m'])
    topology_rows={};endpoints=[]
    if topology['exitcode']==0:
        lines=re.sub(r'\x1b\[[0-9;]*m','',raw).splitlines()
        for line in lines:
            words=line.split()
            if not words:continue
            if not endpoints:
                selected=[w for w in words if re.fullmatch(r'(GPU|NIC)\d+',w)]
                if len(selected)>=len(gpus) and selected and words[0]=='GPU0':endpoints=selected
            elif words[0] in endpoints and len(words)>=1+len(endpoints):
                topology_rows[words[0]]=dict(zip(endpoints,words[1:1+len(endpoints)]))
    devices=[]
    for device in sorted(Path('/sys/class/infiniband').glob('*'))[:32]:
        ports=[]
        for port in sorted((device/'ports').glob('*'))[:8]:
            record=dict(port=port.name)
            for field in ('state','phys_state','rate','link_layer'):
                try:
                    with (port/field).open() as f:value=f.read(4097)
                    assert len(value)<=4096;record[field]=value.strip()
                except OSError:record[field]=None
            record['active']=record['state'] is not None and record['state'].startswith('4: ACTIVE')
            record['active_rate_observed']=record['rate'] if record['active'] else None
            ports.append(record)
        devices.append(dict(device=device.name,ports=ports))
    verbs,raw,_=run(['ibv_devinfo','-l'])
    match=re.search(r'(\d+)\s+HCAs?\s+found',raw)
    verbs_hcas=int(match[1]) if verbs['exitcode']==0 and match else None
    tools={}
    for name in ('nvcc','ib_write_bw','ib_atomic_bw'):
        result,raw,error=run([name,'--version'])
        tools[name]=dict(found_in_PATH=result['state']!='not_found_in_PATH',exitcode=result['exitcode'],
                         version_text=(raw+error).strip()[:1024])
    help_result,raw,error=run(['ib_write_bw','--help'])
    help_text=raw+error
    # This perftest release exits 1 for complete --help/--version output.
    # Preserve that exit code; do not confuse it with an unavailable tool.
    help_recognized=help_result['state']=='completed' and help_result['exitcode'] in (0,1) and help_text.lstrip().startswith('Usage:')
    tools['ib_write_bw']['help_output_recognized']=help_recognized
    tools['ib_write_bw']['help_advertises_use_cuda']='--use_cuda' in help_text if help_recognized else None
    versions=[]
    snippet="""import json
from importlib.metadata import version,PackageNotFoundError
out={}
for name in ('torch','nvidia-nccl-cu12','nvidia-nccl-cu13'):
    try:out[name]=version(name)
    except PackageNotFoundError:out[name]=None
print(json.dumps(out))
"""
    for index,python in enumerate(args.python or [sys.executable]):
        result,raw,_=run([python,'-c',snippet])
        versions.append(dict(environment=index,metadata=json.loads(raw) if result['exitcode']==0 else None,
                             metadata_read_succeeded=result['exitcode']==0))
    # Source requirements are independently pinned; not inferred from tool names
    # or from the driver's advertised maximum CUDA runtime version.
    folder=ROOT/'data/research/deepep_modern_20261022'
    manifests=[json.loads(p.read_text()) for p in folder.glob('file-sources*.json')]
    record=next(r for m in manifests for r in m['sources'] if r['path']=='README.md')
    readme=(folder/'sources/README.md').read_bytes()
    identity=json.loads((folder/'tree-source.json').read_text())
    tree_raw=(folder/'tree.json').read_bytes();tree=json.loads(tree_raw)
    assert identity['commit']==tree['sha']==DEEP and not tree['truncated']
    assert hashlib.sha256(tree_raw).hexdigest()==identity['requests'][1]['sha256']
    entry=next(item for item in tree['tree'] if item['path']=='README.md')
    assert record['status']=='read' and record['commit']==DEEP and hashlib.sha256(readme).hexdigest()==record['sha256']
    blob=hashlib.sha1(b'blob '+str(len(readme)).encode()+b'\0'+readme).hexdigest()
    assert blob==record['git_blob_sha']==entry['sha'] and len(readme)==entry['size']
    lines=readme.decode().splitlines()
    needles=['NVIDIA Hopper or newer GPUs','CUDA Toolkit 13.1','PyTorch 2.10','NCCL 2.32.3']
    source=[]
    for needle in needles:
        matches=[(i+1,line) for i,line in enumerate(lines) if needle in line];assert len(matches)==1
        source.append(dict(first_line=matches[0][0],text=matches[0][1]))
    supported=[g['index'] for g in gpus if re.fullmatch(r'\d+\.\d+',g['compute_capability']) and float(g['compute_capability'])>=9]
    active=sum(p['active'] for d in devices for p in d['ports'])
    uverbs=len(list(Path('/dev/infiniband').glob('uverbs*')))
    def gate(name,state,evidence):return dict(requirement=name,state=state,evidence=evidence)
    gates=[gate('visible Hopper-or-newer candidate GPU','satisfied_necessary_only' if supported else 'failed' if gpus else 'unknown',
                dict(eligible_indices=supported,observed_gpus=len(gpus))),
        gate('usable RDMA device interface','failed' if uverbs==0 or verbs_hcas==0 else 'unknown',
             dict(uverbs_nodes=uverbs,enumerated_hcas=verbs_hcas)),
        gate('active RDMA port','satisfied_necessary_only' if active else 'failed' if devices else 'unknown',dict(active_ports=active)),
        gate('CUDA Toolkit >=13.1 available in inspected PATH','unknown' if tools['nvcc']['found_in_PATH'] else 'failed',
             dict(nvcc_found_in_PATH=tools['nvcc']['found_in_PATH'])),
        gate('PyTorch >=2.10 with CUDA in selected Python','unknown' if any(v['metadata'] and v['metadata']['torch'] for v in versions)
             else 'failed' if all(v['metadata_read_succeeded'] for v in versions) else 'unknown',versions),
        gate('fixed NCCL/provider loaded build and second-node reachability','unknown',
             'Package metadata, driver version and a sysfs rate do not prove provider/runtime identity or a reachable peer.')]
    result=dict(schema_version='moe-calibration-readiness/1',state='read_only_inspection_completed',
        inspected_utc=datetime.now(timezone.utc).isoformat(),gpus=gpus,gpu_nic_relationships=topology_rows,
        gpu_nvlink_pair_observed=any(v.startswith('NV') for node,row in topology_rows.items() if node.startswith('GPU')
            for peer,v in row.items() if peer.startswith('GPU') and peer!=node),
        sysfs_rdma_devices=devices,uverbs_nodes=uverbs,verbs_hcas=verbs_hcas,tools=tools,python_environments=versions,
        modern_requirements=dict(commit=DEEP,readme_sha256=record['sha256'],git_blob_sha=blob,
            public_url=record['public_url'],excerpts=source),gates=gates,
        modern_network_calibration_ready=False, # Necessary inspection cannot validate runtime execution.
        any_benchmark_or_traffic_executed=False,cuda_context_created=False,rdma_context_created=False,
        dependency_changes=False,scope='Only this process/container view at capture time; not total cluster inventory.',
        wall_seconds=time.monotonic()-start)
    save(out/'summary.json',result)
    assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file())<=4*2**20
    print(json.dumps(dict(state=result['state'],visible_gpus=len(gpus),modern_candidate_gpus=len(supported),
        sysfs_rdma_devices=len(devices),active_rdma_ports=active,verbs_hcas=verbs_hcas,uverbs_nodes=uverbs,
        modern_network_calibration_ready=False,any_benchmark_or_traffic_executed=False)))


if __name__=='__main__':main()
