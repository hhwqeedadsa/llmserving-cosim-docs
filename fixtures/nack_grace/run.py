"""Five bounded functional networks, reserving the sixth for a fresh source root."""
import argparse
from copy import deepcopy
from pathlib import Path
import sys
from stage import load,save,sha,run


def normalized(a,b,libraries):
    a,b=deepcopy(a),deepcopy(b)
    for result in (a,b):
        seen=set()
        for i,name in enumerate(result['loaded_libraries']):
            path=str(Path(name).resolve())
            if path in libraries:
                assert sha(name)==libraries[path];result['loaded_libraries'][i]=path;seen.add(path)
        assert seen==set(libraries)
    assert a==b,'default result changed beyond hash-identical library path spelling'
    return dict(complete_result_equal_after_verified_library_path_normalization=True)


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage','old-backend'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();build=load(s/'build.json')
    assert sha(s/'bin/stream-probe')==build['binaries']['stream-probe']
    assert build['inputs'][str(a.old_backend)]==sha(a.old_backend)
    inputs=s/'inputs';inputs.mkdir();(s/'network').mkdir()
    base=load(repo/'examples/scaleout_nack_grace/work.json');proto=load(repo/'examples/scaleout_nack_grace/protocol.json')
    topology=repo/'examples/scaleout_nack_grace/topology.json'
    (inputs/'topology.json').write_bytes(topology.read_bytes())
    default=deepcopy(base);del default['transport_recovery']['reordering']
    small=deepcopy(base)
    small['work']=[dict(id=0,kind='write',stream=2,bytes=4000,after=[]),
        dict(id=1,kind='local',rank=4,duration_ns=100,after=[dict(work=0,event='accepted')]),
        dict(id=2,kind='write',stream=2,bytes=2000,after=[dict(work=0,event='acked'),dict(work=1,event='completed')])]
    healthy=deepcopy(proto);del healthy['link_failures'];del healthy['local_failover']
    cases={}
    for name in ('default-old','default-new','head-loss','no-feedback','mixed','fresh-rto'):
        w=deepcopy(default if name.startswith('default') else base if name=='mixed' else small)
        c=deepcopy(proto if name.startswith('default') or name=='mixed' else healthy)
        if name in ('head-loss','no-feedback','fresh-rto'):
            c['faults']=[dict(node=4,port=0,message_id=2,kind='data',sequence=seq,start_ns=0,end_ns=5000000,count=1)
                for seq in ([0,1000,2000,3000] if name=='no-feedback' else [0])]
        if name=='fresh-rto':w['transport_recovery']['reordering']['wait_ns']=80000
        save(inputs/(name+'-work.json'),w);save(inputs/(name+'-protocol.json'),c)
        cases[name]=dict(work=name+'-work.json',protocol=name+'-protocol.json')
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    run(repo,s,'topology',[*cli,'topology','--config',inputs/'topology.json','--output',s/'topology'])
    from moe_ub_cosim.scaleout_streams import validate
    for name in cases:validate(load(s/'topology/topology.json'),load(inputs/cases[name]['protocol']),load(inputs/cases[name]['work']))
    save(s/'cases.json',cases)
    for name in ('default-old','default-new','head-loss','no-feedback','mixed'):
        backend=a.old_backend if name=='default-old' else s/'bin/stream-probe'
        run(repo,s,name,[*cli,'rdma-stream-probe','--topology',s/'topology','--protocol',inputs/cases[name]['protocol'],
            '--config',inputs/cases[name]['work'],'--backend',backend,'--output',s/'network'/name],network=True)
    comparison=normalized(load(s/'network/default-old/result.json'),load(s/'network/default-new/result.json'),build['libraries'])
    save(s/'run.json',dict(passed=True,networks=5,sixth_reserved='fresh-rto',default=comparison))

if __name__=='__main__':main()
