"""Complete cold-start query workflow; only public CLI executes the platform."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


def read(p): return json.loads(p.read_bytes())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v): p.write_text(json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2)+'\n')


def summary(result):
    if 'rc_concurrent' in result:
        rc=result['rc_concurrent']; counts=Counter(p['kind'] for p in rc['physical_packets'])
        value=dict(finish_ns=result['finish_ns'],connections=rc['connections'],memories=rc['memories'],
            target_effects=rc['target_effects'],physical_tx=counts['tx'],physical_rx=counts['rx'],physical_drop=counts['drop'])
    else:value=result
    return {**{k:value[k] for k in ('finish_ns','connections','target_effects','physical_tx','physical_rx','physical_drop')},
        'memory_hashes':{str(m['gpu']):hashlib.sha256(bytes.fromhex(m['data_hex'])).hexdigest() for m in value['memories']}}


def native(stage, output, work, capture, name, execution):
    result=output/'native'/name
    command=[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rc-fabric-probe',
        '--topology',str(stage/'inputs/topology'), '--protocol',str(stage/('inputs/protocol_capture/rdma.json' if capture else 'inputs/protocol_fast/rdma.json')),
        '--config',str(work),'--backend',str(stage/'runtime/moe-rdma-rc-concurrent'),'--output',str(result)]
    execution['networks'].append(dict(name=name,work=str(work.relative_to(stage)),capture=capture,state='started'))
    save(output/'execution.json',execution)
    process=subprocess.run(command,capture_output=True,timeout=60)
    (output/(name+'.stdout')).write_bytes(process.stdout); (output/(name+'.stderr')).write_bytes(process.stderr)
    execution['networks'][-1]['state']='completed' if process.returncode==0 else 'failed'
    save(output/'execution.json',execution); assert process.returncode==0,process.stderr.decode()
    actual=read(result/'result.json')
    assert actual['passed'] and actual['drained'] and actual['rc_concurrent']['drained']
    if capture:assert all(q['timeouts']==0 for q in actual['rc_concurrent']['connections'])
    return actual,result


def execute(stage, method, output):
    assert not output.exists(); output.mkdir(); (output/'native').mkdir()
    for name,digest in read(stage/'prepared-hashes.json').items():assert sha(stage/name)==digest
    identity=read(stage/'inputs/identity.json')
    assert sha(stage/'runtime/moe-rdma-rc-concurrent')==identity['backend_sha256']
    for name,digest in identity['library_hashes'].items():assert sha(stage/'runtime/runtime/lib'/name)==digest
    queries=read(stage/'inputs/queries.json'); execution=dict(method=method,networks=[],decisions={},closed=False)
    save(output/'execution.json',execution)
    results={}; cache={}; predictions={}
    if method=='reuse':
        entry=read(stage/'inputs/algorithm.json'); algorithm_path=stage/entry['path']; assert sha(algorithm_path)==entry['sha256']
        spec=importlib.util.spec_from_file_location('frozen_port_time',algorithm_path)
        algorithm=importlib.util.module_from_spec(spec); spec.loader.exec_module(algorithm)
        for mode in ('terminal','gated'):
            _,cache[mode]=native(stage,output,stage/'inputs'/mode/'normal.json',True,'normal-'+mode,execution)
        for query in queries:
            mode=query['mode']; observed=output/'queries'/query['id']/'observed'
            folder=observed/mode; (folder/'run').mkdir(parents=True); (observed/'topology').mkdir()
            pairs=[(stage/'inputs/identity.json',observed/'identity.json'),
                (stage/'inputs/protocol_capture/rdma.json',observed/'rdma.json'),
                (stage/'inputs/topology/topology.json',observed/'topology/topology.json'),
                (stage/'inputs'/mode/'normal.json',folder/'normal.json'),
                (stage/'inputs'/query['work'],folder/'loss.json'),
                (cache[mode]/'result.json',folder/'run/result.json'),
                (cache[mode]/'inputs.json',folder/'run/inputs.json')]
            for source,destination in pairs:os.link(source,destination)
            predictions[query['id']]=algorithm.predict(observed,mode)
        save(output/'predictions.json',predictions)
        save(output/'prediction-seal.json',dict(sha256=sha(output/'predictions.json'),
            fault_networks_started=0,normal_networks_completed=2,sealed_utc=datetime.now(timezone.utc).isoformat()))
    for query in queries:
        name=query['id']
        prediction=predictions.get(name)
        if prediction and prediction['admitted']:
            results[name]=summary(prediction); execution['decisions'][name]='admitted'
        else:
            actual,_=native(stage,output,stage/'inputs'/query['work'],False,name,execution)
            results[name]=summary(actual); execution['decisions'][name]='direct' if method=='direct' else 'fallback'
        save(output/'execution.json',execution)
    save(output/'summary.json',results)
    execution['closed']=True; save(output/'execution.json',execution)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--method',choices=('direct','reuse'),required=True); p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(); execute(args.stage.resolve(),args.method,args.output.resolve())
