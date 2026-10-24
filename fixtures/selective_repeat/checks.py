"""Independent malformed input and evidence-tamper rejection checks."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from audit import audit


def load(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser()
    for name in ('stage','backend','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();s=a.stage
    from moe_ub_cosim.scaleout_streams import validate
    topology=load(s/'topology/topology.json');protocol=load(s/'inputs/healthy-protocol.json');work=load(s/'inputs/healthy-work.json')
    variants=[]
    for key,value in [('receive_buffer_bytes',True),('receive_buffer_bytes',4998),('receive_buffer_bytes',2**21+1),
                      ('inflight_bytes',True),('inflight_bytes',999),('inflight_bytes',8192),('max_events',0),
                      ('extra',1),('mode','unknown')]:
        v=deepcopy(work);v['transport_recovery'][key]=value;variants.append((key+'-'+str(value),v))
    for key in ('receive_buffer_bytes','inflight_bytes'):
        v=deepcopy(work);del v['transport_recovery'][key];variants.append(('missing-'+key,v))
    v=deepcopy(work);del v['dcqcn_reference'];variants.append(('missing-reference',v))
    out=s/'rejections';out.mkdir();rejected=[]
    for i,(name,v) in enumerate(variants):
        try:validate(topology,protocol,v)
        except ValueError:pass
        else:raise AssertionError('Python accepted '+name)
        config=out/(str(i)+'.json');save(config,v)
        cmd=[str(a.backend),str(s/'topology/topology.json'),str(s/'inputs/healthy-protocol.json'),str(config),str(out/(str(i)+'-result.json'))]
        with (out/(str(i)+'.stdout.log')).open('w') as stdout,(out/(str(i)+'.stderr.log')).open('w') as stderr:
            result=subprocess.run(cmd,stdout=stdout,stderr=stderr,timeout=8)
        assert result.returncode!=0 and not (out/(str(i)+'-result.json')).exists()
        rejected.append(dict(name=name,python_rejected=True,native_exit_code=result.returncode,command=cmd))
    r=load(s/'network/head/result.json');w=load(s/'inputs/head-work.json');c=load(s/'inputs/head-protocol.json')
    mutations=[]
    v=deepcopy(r);e=next(e for e in v['persistent_transport']['selective_repeat']['events'] if e['kind']=='feedback_received')
    e['extension_hex']='00'+e['extension_hex'][2:];mutations.append(('feedback_wire_bytes',v))
    v=deepcopy(r);e=next(e for e in v['persistent_transport']['selective_repeat']['events'] if e['kind']=='data_received')
    e['receiver_prefix']+=1;mutations.append(('receiver_prefix',v))
    v=deepcopy(r);v['persistent_transport']['work'][0]['accepted_ns']-=1;mutations.append(('premature_work_acceptance',v))
    v=deepcopy(r);v['persistent_transport']['source_packets'][0][5]+=1;mutations.append(('source_sequence',v))
    found=[]
    for name,v in mutations:
        try:audit(v,w,c)
        except (AssertionError,KeyError,ValueError):found.append(name)
        else:raise AssertionError('undetected evidence mutation '+name)
    save(a.output,dict(passed=True,malformed_inputs=rejected,rejected_mutations=found,
        no_network_simulator_run_for_rejected_inputs=True))
    print(json.dumps(dict(passed=True,malformed_classes=len(rejected),evidence_mutations=len(found))))


if __name__=='__main__':main()
