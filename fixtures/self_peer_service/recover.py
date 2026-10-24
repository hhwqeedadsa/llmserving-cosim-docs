"""Fourth network only: restored source, empty cwd and bundled two-entry runtime."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from fixtures.self_peer_service.audit import audit
from fixtures.self_peer_service.stage import load,save,sha


def main():
    p=argparse.ArgumentParser()
    for name in ('restored','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();root=a.restored.resolve();s=a.stage.resolve();source=root/'source';evidence=root/'evidence';cwd=root/'empty-cwd'
    assert Path(__file__).resolve().is_relative_to(source) and not any(cwd.iterdir())
    import moe_ub_cosim
    assert Path(moe_ub_cosim.__file__).resolve().is_relative_to(source)
    env=dict(os.environ,PYTHONPATH=str(source/'python')+':'+str(source),PYTHONDONTWRITEBYTECODE='1')
    commands=[]
    def invoke(label,argv):
        argv=list(map(str,argv))
        with (s/(label+'.stdout')).open('w') as out,(s/(label+'.stderr')).open('w') as err:
            result=subprocess.run(argv,cwd=cwd,env=env,stdout=out,stderr=err,timeout=55)
        commands.append(dict(label=label,command=argv,cwd=str(cwd),exit_code=result.returncode));save(s/'recovery-commands.json',commands)
        assert result.returncode==0,label
    def cli(label,*args):invoke(label,[sys.executable,'-P','-B','-m','moe_ub_cosim','scaleout',*args])
    invoke('restored-audit',[sys.executable,'-P','-B','-m','fixtures.self_peer_service.audit',
        '--stage',evidence,'--output',s/'restored-verification.json'])
    assert (s/'restored-verification.json').read_bytes()==(evidence/'verification.json').read_bytes()
    cfg=load(source/'examples/scaleout_self_peer/program.json')
    cfg['connections']=cfg['connections'][:2];cfg['connections'][1]['self_service']=dict(apply_ns=0,complete_ns=0)
    cfg['operations']=[o for o in cfg['operations'] if o['id'] in (0,2,3)]
    cfg['submission_model']=dict(mode='explicit_prefix/1')
    for op in cfg['operations']:op['aggregate_requests']=op['id']==2
    cfg['operations'].append(dict(id=4,kind='remote',connection=1,verb='put',key=7,address=4120,
        source=dict(key=7,address=4096,bytes=8),aggregate_requests=False,after=[dict(operation=3,event='completed')]))
    save(s/'fresh-input.json',cfg)
    cli('fresh-topology','topology','--config',source/'examples/scaleout_context_transactions/topology.json','--output',s/'fresh-topology')
    cli('fresh-smoke','rdma-transaction-probe','--topology',s/'fresh-topology','--protocol',source/'examples/scaleout_self_peer/rdma.json',
        '--config',s/'fresh-input.json','--backend',evidence/'runtime/run-transaction-probe','--output',s/'fresh-smoke')
    result=load(s/'fresh-smoke/result.json');report=audit(result,cfg)
    assert report['atomic_returns']==[5,6] and report['final_values']=={'0':0,'4':8}
    assert report['self_operations']==3 and report['remote_operations']==1 and report['retransmitted_packets']==0
    rows=result['transactions']['operations'];selfrows=[o for o in rows if o['connection']==1]
    assert len({o['published_ns'] for o in selfrows})==1
    assert all(o['published_ns']==o['applied_ns']==o['completed_ns'] for o in selfrows)
    source_events=result['transactions']['source_events']
    assert next(e for e in source_events if e['kind']=='captured')['hex']=='0800000000000000'
    libraries=[Path(p) for p in result['loaded_libraries'] if Path(p).name.startswith('libns3')]
    assert len(libraries)==8 and all(p.parent==evidence/'runtime/lib' and sha(p)==sha(s/'runtime/lib'/p.name) for p in libraries)
    assert all(sha(evidence/'runtime'/name)==sha(s/'runtime'/name) for name in ('transaction-probe','transaction-session'))
    save(s/'recovery.json',dict(passed=True,restored_source_root=str(source),cwd=str(cwd),
        offline_reports_byte_equal=1,new_network_executions=1,old_network_reexecutions=0,
        zero_duration_prefix_and_source_self_executed=True,actual_bundled_ns3_libraries=[str(p) for p in libraries],smoke=report,
        scope='same-host ABI-bound runtime, new Python source root and empty cwd; not a clean native build or GIN/GPU calibration'))


if __name__=='__main__':main()
