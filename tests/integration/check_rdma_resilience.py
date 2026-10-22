"""Real native gates for source history, destination shares and service feedback."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
EXAMPLE=ROOT/'examples/scaleout_rdma_resilience'
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend-json',type=Path,required=True);p.add_argument('--protoc',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output.resolve();out.mkdir(exist_ok=False);backend=read(a.backend_json);commands=[]
    def cli(name,args,success=True):
        assert shutil.disk_usage(out).free>=8*1024**3
        assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file())<256*1024**2
        argv=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,args)]
        with (out/(name+'.stdout')).open('w') as f,(out/(name+'.stderr')).open('w') as e:
            completed=subprocess.run(argv,cwd=ROOT,stdout=f,stderr=e,timeout=90)
        commands.append(dict(argv=argv,cwd=str(ROOT),exit_code=completed.returncode,expected_success=success))
        save(out/'commands.json',commands)
        assert (completed.returncode==0)==success,name
    cli('topology',['topology','--config',ROOT/'examples/scaleout_rdma/small-clos-100g.json','--output',out/'topology'])
    def probe(name,config,messages,success=True,topology=None):
        save(out/(name+'.json'),config)
        cli(name,['rdma-probe','--topology',topology or out/'topology','--config',out/(name+'.json'),
            '--messages',messages,'--backend',backend['probe'],'--output',out/name],success)
        cli(name+'-diagnose',['rdma-diagnose','--run',out/name,'--output',out/(name+'-diagnosis.json')])
        return read(out/name/'result.json'),read(out/(name+'-diagnosis.json'))

    history=read(EXAMPLE/'history.json');messages=EXAMPLE/'history-messages.json'
    no_history=deepcopy(history);no_history['recovery'].pop('history')
    baseline,_=probe('history-off',no_history,messages)
    warm,diagnosis=probe('history-on',history,messages)
    s={s['id']:s for s in warm['recovery']['states']}
    assert not s[50000]['history_hit'] and s[50000]['rtt']['samples']==0
    assert s[50001]['history_hit'] and s[50001]['seed_rto_ns']>history['recovery']['initial_rto_ns']
    assert not s[50002]['history_hit'] and not s[50003]['history_hit'] # Source and destination isolation.
    assert s[50004]['history_hit'] and not s[50005]['history_hit'] # Evidence expiry, not sliding read age.
    assert diagnosis['recovery']['rtt']['history_reconstructed'] and warm['recovery']['history']['expirations']>0
    for cap in [0,3]:
        c=deepcopy(history);c['recovery']['max_events']=cap
        observed,d=probe('history-capture-'+str(cap),c,messages)
        for key in ['messages','ports','service','queue_events']:
            if key in warm:assert observed[key]==warm[key]
        for key in ['states','history']:assert observed['recovery'][key]==warm['recovery'][key]
        assert not d['recovery']['rtt']['reconstructed']
    c=deepcopy(history);c['recovery']['history']['max_entries_per_source']=1
    small,d=probe('history-eviction',c,messages)
    assert small['recovery']['history']['evictions']>0 and d['recovery']['rtt']['history_reconstructed']
    isolated=[dict(id=i,src=src,dst=dst,bytes=1000,plane=0,ready_ns=ready)
              for i,src,dst,ready in [(0,0,4,0),(1,1,6,100000),(2,0,4,200000)]]
    save(out/'isolated-sources.json',isolated)
    isolated_r,_=probe('history-independent-sources',c,out/'isolated-sources.json')
    isolated_states={s['id']:s for s in isolated_r['recovery']['states']}
    assert isolated_states[2]['history_hit'] # Source1 must not evict source0's one-entry cache.

    protected=read(EXAMPLE/'destination-protection.json');messages=EXAMPLE/'protection-messages.json'
    unprotected=deepcopy(protected);unprotected['admission'].pop('per_destination')
    stalled,stalled_d=probe('protection-off',unprotected,messages,False)
    progressing,progress_d=probe('protection-on',protected,messages,False)
    f={f['id']:f for f in stalled['messages']};g={f['id']:f for f in progressing['messages']}
    assert f[50002]['admission_granted_ns'] is None
    assert g[50002]['ack_callbacks']==1 and g[50002]['sender_resource_released_ns'] is not None
    assert g[50001]['admission_granted_ns'] is None and g[50000]['sender_resource_released_ns'] is None
    assert progress_d['source_credit_obstructions']['sources'][0]['reason']=='destination_shares_full'
    assert progress_d['admission_resources']['reconstructed']
    for name,cfg in [('protection-clean-off',unprotected),('protection-clean-on',protected)]:
        c=deepcopy(cfg);c.pop('faults');probe(name,c,messages)

    # Two destination addresses on distinct planes must not share warm seeds.
    dual=read(ROOT/'examples/scaleout_rdma/small-clos-100g.json');dual['deployment']['pods']=1
    dual['fabric'].update(planes=2,aggs_per_pod_per_group=1,cores_per_group=0,tor_uplinks=1,agg_uplinks=0,core_wiring='none')
    dual['nic']['total_rate_bps']=200000000000;save(out/'dual-topology.json',dual)
    cli('dual-topology',['topology','--config',out/'dual-topology.json','--output',out/'dual-topology'])
    rows=[dict(id=i,src=0,dst=2,bytes=1000,plane=plane,ready_ns=ready) for i,plane,ready in [(0,0,0),(1,1,100000),(2,0,100000)]]
    save(out/'dual-messages.json',rows)
    dual_r,_=probe('history-dual-plane',history,out/'dual-messages.json',topology=out/'dual-topology')
    ss={s['id']:s for s in dual_r['recovery']['states']}
    assert not ss[1]['history_hit'] and ss[2]['history_hit']

    # Actual frontend, not a set of independently timed network calls.
    combined=read(EXAMPLE/'combined-service.json');reference=None;services=[]
    for use_history,use_shares in [(False,False),(True,False),(False,True),(True,True)]:
        name=f'service-history{int(use_history)}-shares{int(use_shares)}';c=deepcopy(combined)
        if not use_history:c['recovery'].pop('history')
        if not use_shares:c['admission'].pop('per_destination')
        save(out/(name+'.json'),c)
        cli(name,['rdma-run','--topology',out/'topology','--config',EXAMPLE/'service.json',
            '--protocol',out/(name+'.json'),'--backend',backend['service'],'--protoc',a.protoc,'--output',out/name])
        cli(name+'-diagnose',['rdma-diagnose','--run',out/name,'--output',out/(name+'-diagnosis.json')])
        cli(name+'-check',['rdma-check','--run',out/name])
        r=read(out/name/'result.json');d=read(out/(name+'-diagnosis.json'))
        graphs={p.parent.name:p.read_bytes() for p in (out/name/'graphs').glob('*/wave.json')}
        if reference is None:reference=graphs
        assert graphs==reference and r['passed'] and r['drained'] and r['service']['service_finished']
        if use_history:assert d['recovery']['rtt']['history_reconstructed']
        services.append(name)
    save(out/'summary.json',dict(passed=True,probe_cases=11,service_cases=len(services),
        source_destination_plane_isolation=True,expiry_and_eviction=True,zero_sample_timeout_history=True,
        capture_independence=True,destination_share_ownership=True,healthy_network_work_progress=True,
        request_failure_isolation=False,common_service_graphs_identical=True))
    print('Native history, destination shares and shared-frontend gates passed')


if __name__=='__main__':main()
