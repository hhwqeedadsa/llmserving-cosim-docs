"""Native sparse memory, dense differential and actual high-offset byte replay."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from check_remote_transaction_workflow import audit
from check_transaction_session_workflow import validate_events
from moe_ub_cosim.modern_deepep_plan import token_bytes
from moe_ub_cosim.modern_deepep_windows import ModernDeepEPWindows

ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('backend','session-backend','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(exist_ok=False);started=time.monotonic();commands=[]
    env=dict(os.environ,PYTHONPATH=str(ROOT/'python'),PYTHONDONTWRITEBYTECODE='1')
    def invoke(name,command,expected=0):
        with (out/(name+'.stdout')).open('w') as stdout,(out/(name+'.stderr')).open('w') as stderr:
            s=subprocess.run(list(map(str,command)),cwd=out,env=env,stdout=stdout,stderr=stderr,timeout=20)
        commands.append(dict(name=name,command=list(map(str,command)),cwd=str(out),exitcode=s.returncode,expected=expected))
        save(out/'commands.json',commands);assert s.returncode==expected,name
    cli=[sys.executable,'-m','moe_ub_cosim','scaleout']
    save(out/'topology-input.json',read(ROOT/'examples/scaleout_rdma/small-clos-100g.json'))
    protocol=read(ROOT/'examples/scaleout_rdma/rdma.json');protocol.update(payload_bytes=64,chunk_bytes=256)
    save(out/'protocol.json',protocol)
    invoke('topology',cli+['topology','--config','topology-input.json','--output','topology'])
    def run(name,config,session=False):
        save(out/(name+'.json'),config)
        invoke(name,cli+['rdma-transaction-session' if session else 'rdma-transaction-probe',
            '--topology','topology','--protocol','protocol.json','--config',name+'.json',
            '--backend',a.session_backend.resolve() if session else a.backend.resolve(),'--output',name])
        result=read(out/name/'result.json'); audit(result,config,64)
        if session:validate_events(result)
        return result
    dense=read(ROOT/'examples/scaleout_rdma_transactions/local-io.json')
    sparse=deepcopy(dense);sparse['schema_version']='moe-rdma-transactions/5'
    for w in sparse['windows']:
        data=w.pop('hex');w.update(bytes=len(data)//2,initialization='zero_fill/1',segments=[dict(offset=0,hex=data)])
    rd=run('dense',dense);rs=run('sparse',sparse)
    normalized=deepcopy(rs)
    for w in normalized['transactions']['windows']:
        data=bytearray(w.pop('bytes'))
        for page in w.pop('pages'):
            payload=bytes.fromhex(page['hex']);data[page['offset']:page['offset']+len(payload)]=payload
        del w['resident_bytes'],w['initialization'];w['hex']=data.hex()
    assert normalized==rd # All packets, bytes, timing, leases and queue state.
    high=read(ROOT/'examples/scaleout_rdma_transactions/sparse-windows.json')
    # Independently connect this input's logical extent and staging offset to
    # the public modern layout, without claiming to execute its protocol.
    config=read(ROOT/'examples/modern_deepep_partial_order/input.json')
    config['model'].update(experts=256,top_k=8,hidden_size=7168)
    for call in config['calls']:call['tokens']=[[[0,1,32,33,64,65,96,97]],[],[],[]]
    layout=ModernDeepEPWindows(config).describe();payload=token_bytes(7168,8,True)
    assert payload==14464 and all(w['bytes']==layout['main_window']['bytes'] for w in high['windows'])
    assert high['operations'][0]['source']['address']-2**32==16*2**20+16*payload
    rh=run('high',high);online=run('high-online',high,True)
    # Causal frontend uses the same actual dependencies and shared network.
    plain=deepcopy(online);del plain['session'];assert plain==rh
    changed=deepcopy(high);changed['windows'][0]['segments'][0]['hex']='fe'+changed['windows'][0]['segments'][0]['hex'][2:]
    assert changed['operations']==high['operations']
    rc=run('high-changed',changed)
    for result,value in [(rh,1),(rc,254)]:
        captures=[e for e in result['transactions']['source_events'] if e['kind']=='captured']
        assert len(captures)==2 and all(bytes.fromhex(e['hex'])[0]==value for e in captures)
        assert bytes.fromhex(next(e['hex'] for e in result['transactions']['local_memory_events'] if e['kind']=='committed'))[0]==value
    rejected=[]
    for name,change in [
        ('bool-size',lambda w:w.update(bytes=True)),('huge-size',lambda w:w.update(bytes=64*2**20+1)),
        ('unknown-fill',lambda w:w.update(initialization='unknown')),
        ('overlap',lambda w:w.update(segments=[dict(offset=0,hex='0000'),dict(offset=1,hex='ff')])),
        ('past-end',lambda w:w.update(segments=[dict(offset=w['bytes'],hex='00')])),
        ('too-many-segments',lambda w:w.update(segments=[dict(offset=i,hex='00') for i in range(33)])),
        ('too-many-initial-bytes',lambda w:w.update(segments=[dict(offset=0,hex='00'*32769),dict(offset=40000,hex='00'*32768)])),
        ('address-overflow',lambda w:w.update(address=2**64-8))]:
        bad=deepcopy(high);change(bad['windows'][0]);save(out/(name+'.json'),bad)
        for flavor,binary in [('probe',a.backend),('session',a.session_backend)]:
            result=out/(name+'-'+flavor+'-result.json')
            initial=dict(bad,operations=[]) if flavor=='session' else bad
            save(out/(name+'-'+flavor+'.json'),initial)
            invoke(name+'-'+flavor,[binary.resolve(),out/'topology/topology.json',out/'protocol.json',
                                    out/(name+'-'+flavor+'.json'),result],1)
            assert not result.exists() # Pure declaration rejection, no network run.
            rejected.append(name+'-'+flavor)
    logical=sum(w['bytes'] for w in rh['transactions']['windows'])
    resident=sum(w['resident_bytes'] for w in rh['transactions']['windows'])
    summary=dict(passed=True,public_commands=6,direct_native_rejections=rejected,dense_sparse_all_other_fields_equal=True,
        high_offset_online_all_other_fields_equal=True,modern_layout_bytes=layout['main_window']['bytes'],
        logical_window_bytes=logical,resident_page_bytes=resident,actual_data_bytes_per_put=payload,
        high_offset_source_packets=len(rh['transactions']['transport']['source_packets']),
        changed_initial_bytes_changed_actual_forwarding=True,modern_deepep_connected=False,hardware_calibrated=False,
        wall_seconds=time.monotonic()-started,
        binary_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (a.backend,a.session_backend)})
    save(out/'verification.json',summary);print(json.dumps(summary))

if __name__=='__main__':main()
