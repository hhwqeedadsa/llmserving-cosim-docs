"""Effective-attribute provenance must preserve legacy probe/service execution."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time

from moe_ub_cosim.host_probe import probe

ROOT=Path(__file__).resolve().parents[2]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend-dir',type=Path,required=True)
    p.add_argument('--reference-dir',type=Path,required=True)
    p.add_argument('--protoc',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--mode',choices=('all','probe','service'),default='all')
    a=p.parse_args();out=a.output.resolve();out.mkdir(exist_ok=False)
    example=ROOT/'examples/scaleout_rdma';start=time.monotonic();commands=[]
    def read(path):return json.loads(path.read_text())
    def save(name,value):(out/name).write_text(json.dumps(value,indent=2)+'\n')
    def cli(name,argv):
        assert time.monotonic()-start<60
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,argv)]
        usage=probe(command,out/(name+'-monitor'),rss_bytes=2**30,address_bytes=2*2**30,
                    artifact_bytes=32*2**20,wall_seconds=60,artifact_roots=[out/name],min_free_bytes=8*2**30)
        commands.append(usage);save('commands.json',commands)
        assert usage['status']=='completed',name
    cli('topology',['topology','--config',example/'small-clos-100g.json','--output',out/'topology'])
    small=read(example/'service.json');small['model']['layers']=1;small['workload']['decode_steps']=2
    save('service.json',small)
    verified={}
    for kind in (('probe','service') if a.mode=='all' else (a.mode,)):
        for name,directory in [('old',a.reference_dir),('new',a.backend_dir)]:
            binary=directory.resolve()/('ns3.42-moe-rdma-'+kind);dest=out/(kind+'-'+name)
            if kind=='probe':
                argv=['rdma-probe','--config',example/'rdma.json','--messages',example/'messages.json']
            else:argv=['rdma-run','--config',out/'service.json','--protocol',example/'rdma.json','--protoc',a.protoc]
            cli(kind+'-'+name,[*argv,'--topology',out/'topology','--backend',binary,'--output',dest])
        old,new=[read(out/(kind+'-'+name)/'result.json') for name in ('old','new')]
        original=deepcopy(new);parameters=original.pop('rdma_parameters')
        assert original==old and new['passed'] and new['drained']
        assert len(parameters['hosts'])==8 and parameters['units']['RPTimer']=='us'
        assert all(h['attributes']['RPTimer']==1500 and h['attributes']['MinRate']==100000000 for h in parameters['hosts'])
        info=dict(network_identical_after_only_new_metadata_removed=True,finish_ns=new['finish_ns'],messages=len(new['messages']))
        if kind=='service':
            left,right=[out/('service-'+name)/'log/astra_nodes.log' for name in ('old','new')]
            assert left.read_bytes()==right.read_bytes();info['astra_node_log_byte_identical']=True
            def normalize(name):
                path=out/('service-'+name)
                return json.dumps(read(path/'frontend.json'),sort_keys=True).replace(str(path),'RUN')
            assert normalize('old')==normalize('new');info['frontend_equal_after_path_normalization']=True
        verified[kind]=info
        save('progress.json',verified)
    verified.update(passed=True,hardware_calibrated=False,public_commands=len(commands),wall_seconds=time.monotonic()-start,
                    source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    save('verification.json',verified);print(json.dumps(verified))


if __name__=='__main__':main()
