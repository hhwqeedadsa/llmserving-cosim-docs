"""Public source-state capture acceptance; artificial traffic, no calibration."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]


def network_only(result):
    result=deepcopy(result)
    result.pop('rdma_parameters',None)
    result['persistent_transport'].pop('source_rate_capture',None)
    return result


def audit(result):
    assert result['passed'] and result['drained']
    transport=result['persistent_transport'];capture=transport['source_rate_capture']
    rows=[dict(zip(capture['columns'],r)) for r in capture['samples']]
    assert len(rows)<=capture['max_events']
    assert all(capture['start_ns']<=r['time_ns']<capture['end_ns'] for r in rows)
    # Each TX sample must be an actual source packet, not an offered write.
    expected={(p[0],p[1],p[7],p[5]):p for p in transport['source_packets']
              if capture['start_ns']<=p[0]<capture['end_ns']}
    tx=[r for r in rows if r['event']==0]
    assert set(expected)=={(r['time_ns'],r['stream'],r['packet_uid'],r['sequence']) for r in tx}
    assert len(tx)==len(expected)
    streams={s['id']:s for s in transport['streams']}
    endpoints={s['src'] for s in streams.values()}
    events=result['packet_capture'];assert events['complete']
    packets=[dict(zip(events['columns'],p)) for p in events['events']]
    source_tx={(p['time_ns'],p['node'],p['packet_uid'],p['sequence']):p for p in packets
               if p['event']==0 and p['ip_protocol']==17 and p['node'] in endpoints}
    for r in tx:
        spec=streams[r['stream']]
        packet=source_tx[r['time_ns'],spec['src'],r['packet_uid'],r['sequence']]
        p=expected[r['time_ns'],r['stream'],r['packet_uid'],r['sequence']]
        assert r['model_bytes']==packet['model_bytes'] and r['model_bytes']>p[6]
        assert r['snd_nxt']==r['sequence']+p[6]<=r['published_bytes']
        assert r['time_ns']>=r['next_avail_ns']
    before=[r for r in rows if r['event']==1];after=[r for r in rows if r['event']==2]
    assert len(before)==len(after)
    # Native ReceiveUdp copies each packet's nonzero ECN into the ACK CNP bit.
    source_by_uid={p[7]:s for s in streams.values() for p in transport['source_packets'] if p[1]==s['id']}
    target={(source_by_uid[p['packet_uid']]['id'],p['sequence']+p['data_payload_bytes']):p
            for p in packets if p['event']==1 and p['ip_protocol']==17 and
            p['packet_uid'] in source_by_uid and p['node']==source_by_uid[p['packet_uid']]['dst']}
    ack_rx={(p['time_ns'],p['node'],p['packet_uid'],p['sequence']):p for p in packets
            if p['event']==1 and p['ip_protocol']==252 and p['node'] in endpoints}
    covered=[p for p in ack_rx if capture['start_ns']<=p[0]<capture['end_ns']]
    assert len(before)==len(covered)
    first_cnp=0
    for a,b in zip(before,after):
        keys=('time_ns','stream','packet_uid','sequence','model_bytes','ack_cnp')
        assert all(a[k]==b[k] for k in keys)
        spec=streams[a['stream']]
        packet=ack_rx[a['time_ns'],spec['src'],a['packet_uid'],a['sequence']]
        assert a['model_bytes']==packet['model_bytes']
        assert a['ack_cnp']==int(target[a['stream'],a['sequence']]['ecn']!=0)
        assert b['snd_una']==max(a['snd_una'],a['sequence'])
        assert b['snd_nxt']==a['snd_nxt'] and b['published_bytes']==a['published_bytes']
        if a['ack_cnp']:
            assert b['decrease_cnp_arrived'] and not b['first_cnp']
            if a['first_cnp']:
                first_cnp+=1
                assert b['alpha']==1 and not b['alpha_cnp_arrived']
                assert b['rate_bps']==a['rate_bps']==b['target_rate_bps']  # fixed first-CNP fraction 1
            else:assert b['alpha_cnp_arrived']
        else:
            for key in capture['state_columns']:
                if key!='snd_una':assert a[key]==b[key],key
    for r in rows:
        assert 100000000<=r['rate_bps']<=r['max_rate_bps']==100000000000
        assert 0<=r['alpha']<=1 and r['snd_una']<=r['snd_nxt']<=r['published_bytes']
    assert capture['final_time_ns']==result['finish_ns']
    for item in capture['final_states']:
        r=dict(zip(capture['state_columns'],item['state']));s=streams[item['stream']]
        assert r['snd_nxt']==r['snd_una']==r['published_bytes']==s['submitted_bytes']
    parameters=result['rdma_parameters']
    assert parameters['sample_phase']=='after_driver_init_before_QPs' and not parameters['hardware_calibrated']
    assert len(parameters['hosts'])==result['gpu_count']
    for host in parameters['hosts']:
        a=host['attributes']
        assert a['MinRate']==100000000 and a['RPTimer']==1500 and a['RateBound']
        assert a['RateDecreaseInterval']==4 and a['AlphaResumInterval']==55
        assert a['RateOnFirstCnp']==1 and a['EwmaGain']==1/16
        assert a['FastRecoveryTimes']==5 and not a['ClampTargetRate']
        assert a['RateAI']==5000000 and a['RateHAI']==50000000
    return dict(source_packets=len(tx),ack_pairs=len(before),first_cnp_transitions=first_cnp,
                cnp_ack_pairs=sum(r['ack_cnp'] for r in before),sample_count=len(rows),
                minimum_rate_at_tx_bps=min((r['rate_bps'] for r in tx),default=None),
                minimum_final_rate_bps=min(r['state'][0] for r in capture['final_states']),
                finish_ns=result['finish_ns'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',type=Path,required=True)
    p.add_argument('--reference-backend',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();start=time.monotonic();commands=[]
    assert not out.exists() and shutil.disk_usage(out.parent).free>=8*2**30+32*2**20
    out.mkdir();env=dict(os.environ,PYTHONPATH=str(ROOT/'python'),PYTHONDONTWRITEBYTECODE='1')
    def save(name,value):
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    def cli(name,args,expected=0):
        assert time.monotonic()-start<180
        cmd=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,args)]
        began=time.monotonic()
        with (out/(name+'.stdout')).open('w') as stdout,(out/(name+'.stderr')).open('w') as stderr:
            run=subprocess.run(cmd,cwd=out,env=env,stdout=stdout,stderr=stderr,timeout=65)
        commands.append(dict(command=cmd,cwd=str(out),exitcode=run.returncode,expected=expected,wall_seconds=time.monotonic()-began))
        save('commands.json',commands);assert run.returncode==expected,name
    shutil.copy2(ROOT/'examples/scaleout_rdma/small-clos-100g.json',out/'topology-input.json')
    cli('topology',['topology','--config','topology-input.json','--output','topology'])
    protocol=json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
    protocol['queue_metrics']=True
    protocol['packet_capture']=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000)
    normal=json.loads((ROOT/'examples/scaleout_rdma_streams/work.json').read_text())
    ecn=dict(schema_version='moe-rdma-stream-work/1',streams=[dict(id=i,src=s,dst=6,plane=0,
             wire_port=22000+i,source_nic=1,destination_nic=1,queue_depth=1) for i,s in enumerate((0,2,4))],
             work=[dict(id=i,stream=i,bytes=65536,after=[]) for i in range(3)]+
                  [dict(id=i+3,stream=i,bytes=4096,after=[dict(work=i,event='acked')]) for i in range(3)])
    def run(name,work,proto,backend,expected=0):
        save(name+'-work.json',work);save(name+'-protocol.json',proto)
        cli(name,['rdma-stream-probe','--topology','topology','--protocol',name+'-protocol.json',
                  '--config',name+'-work.json','--backend',backend.resolve(),'--output',name],expected)
        if not expected:return json.loads((out/name/'result.json').read_text())
    verified={}
    for name,work in [('normal',normal),('ecn',ecn)]:
        proto=deepcopy(protocol)
        if name=='ecn':proto['ecn_by_rate']['100000000000']=dict(kmin_bytes=1000,kmax_bytes=2000,pmax=1)
        old=run(name+'-old',work,proto,a.reference_backend)
        off=run(name+'-off',work,proto,a.backend)
        on_work=deepcopy(work);on_work['source_rate_capture']=dict(start_ns=0,end_ns=5000000,max_events=10000)
        on=run(name+'-on',on_work,proto,a.backend)
        assert network_only(old)==network_only(off)==network_only(on)
        assert 'source_rate_capture' not in off['persistent_transport']
        info=audit(on);info['old_off_on_network_identical']=True
        if name=='ecn':assert info['cnp_ack_pairs']>0 and info['first_cnp_transitions']>0
        else:assert info['cnp_ack_pairs']==0
        verified[name]=info
        repeat=run(name+'-repeat',on_work,proto,a.backend)
        assert on==repeat
        if name=='normal':
            window=deepcopy(on_work);window['source_rate_capture']=dict(start_ns=1,end_ns=20000,max_events=10000)
            narrow=run('window',window,proto,a.backend);audit(narrow)
            assert network_only(on)==network_only(narrow)
            expected=[r for r in on['persistent_transport']['source_rate_capture']['samples'] if 1<=r[0]<20000]
            assert narrow['persistent_transport']['source_rate_capture']['samples']==expected
            overflow=deepcopy(on_work);overflow['source_rate_capture']['max_events']=1
            run('overflow',overflow,proto,a.backend,1)
            assert 'source rate observation budget exceeded' in (out/'overflow/execution/stderr.log').read_text()
            assert not (out/'overflow/result.json').exists()
            bad=deepcopy(on_work);bad['source_rate_capture']['max_events']=True
            run('invalid',bad,proto,a.backend,1);assert not (out/'invalid').exists()
            # The native boundary must reject invalid fields if CLI is bypassed.
            command=[str(a.backend.resolve()),str(out/'topology/topology.json'),str(out/'invalid-protocol.json'),
                     str(out/'invalid-work.json'),str(out/'native-rejected.json')]
            run_native=subprocess.run(command,cwd=out,capture_output=True,text=True,timeout=10)
            (out/'native-rejection.stderr').write_text(run_native.stderr)
            assert run_native.returncode==1 and 'fields must be integers' in run_native.stderr
            assert not (out/'native-rejected.json').exists()
            commands.append(dict(command=command,cwd=str(out),exitcode=1,expected=1))
            save('commands.json',commands)
    total=sum(f.stat().st_size for f in out.rglob('*') if f.is_file())
    assert total<32*2**20 and time.monotonic()-start<180
    verified.update(passed=True,hardware_calibrated=False,public_commands=len(commands)-1,
                    native_rejections=1,preserved_expected_failures=3,output_bytes=total,
                    wall_seconds=time.monotonic()-start,repeat_identical=True,window_filter_exact=True,
                    backend_sha256=hashlib.sha256(a.backend.read_bytes()).hexdigest(),
                    reference_backend_sha256=hashlib.sha256(a.reference_backend.read_bytes()).hexdigest())
    save('verification.json',verified);print(json.dumps(verified))


if __name__=='__main__':main()
