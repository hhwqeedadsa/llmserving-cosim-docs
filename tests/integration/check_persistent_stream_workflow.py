"""Actual native shared-network acceptance, with independent byte/range checks."""
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


def audit(result,config,mtu):
    assert result['passed'] and result['native_network_executed'] and result['drained']
    t=result['persistent_transport'];assert t['physical_drained'] and t['closed'] and t['inflight_packets']==0
    assert not t['gpu_visibility_modeled'] and not t['atomic_operations_supported'] and not t['hardware_calibrated']
    work={w['id']:w for w in t['work']};streams={s['id']:s for s in t['streams']}
    expected={w['id']:w for w in config['work']};assert set(work)==set(expected)
    end={s['id']:0 for s in config['streams']};packets={i:[] for i in end}
    packet_count=0
    for entry in t['source_packets']:
        stamp,stream,src,dst,nic,sequence,payload,uid=entry
        spec=next(s for s in config['streams'] if s['id']==stream)
        assert (src,dst,nic)==(spec['src'],spec['dst'],spec['source_nic'])
        packets[stream].append(entry);assert uid>=0
    for w in config['work']:
        row=work[w['id']];stream=w['stream']
        assert row['bytes']==w['bytes'] and row['begin']==end[stream] and row['end']==end[stream]+w['bytes']
        end[stream]=row['end']
        ready=max((work[d['work']][d['event']+'_ns'] for d in w['after']),default=0)
        assert row['ready_ns']==ready
        assert ready<=row['published_ns']<=row['accepted_ns']<row['acked_ns']<=result['finish_ns']
        observed=[p for p in packets[stream] if row['begin']<=p[5]<row['end']]
        seq=row['begin'];sizes=[]
        while seq<row['end']:
            size=min(mtu,row['end']-seq);sizes.append((seq,size));seq+=size
        assert [(p[5],p[6]) for p in observed]==sizes
        assert all(row['published_ns']<=p[0]<=row['accepted_ns'] for p in observed)
        packet_count+=len(observed)
    for s in config['streams']:
        row=streams[s['id']];total=end[s['id']]
        assert row['qp_creations']==1 and row['submitted_bytes']==row['published_bytes']==row['snd_nxt']==row['snd_una']==total
        assert row['source_payload_bytes']==total==sum(p[6] for p in packets[s['id']])
        assert row['source_data_packets']==len(packets[s['id']])
        assert row['accepted_work']==row['ack_completed_work']==sum(w['stream']==s['id'] for w in config['work'])
    native=[p for p in result['loaded_libraries'] if Path(p).name.startswith('libns3')]
    assert native and all('unified-bus' not in p for p in native)
    assert len({str(Path(p).parent) for p in native})==1
    return dict(work=len(work),source_packets=packet_count,payload_bytes=sum(end.values()),
                finish_ns=result['finish_ns'],native_libraries=native)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();backend=a.backend.resolve()
    assert not out.exists() and backend.is_file() and shutil.disk_usage(out.parent).free>=8*2**30+32*2**20
    out.mkdir();start=time.monotonic();commands=[]
    env=dict(os.environ,PYTHONPATH=str(ROOT/'python'),PYTHONDONTWRITEBYTECODE='1')
    for source,name in [('examples/scaleout_rdma/small-clos-100g.json','topology-input.json'),
                        ('examples/scaleout_rdma/rdma.json','protocol.json'),
                        ('examples/scaleout_rdma_streams/work.json','work.json')]:
        shutil.copy2(ROOT/source,out/name)
    def cli(name,arguments,expected=0):
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*arguments]
        remaining=290-(time.monotonic()-start);assert remaining>0
        began=time.monotonic()
        with (out/(name+'.stdout')).open('w') as stdout,(out/(name+'.stderr')).open('w') as stderr:
            run=subprocess.run(command,cwd=out,env=env,stdout=stdout,stderr=stderr,timeout=min(65,remaining))
        commands.append(dict(argv=list(map(str,command)),cwd=str(out),exitcode=run.returncode,
                             expected_exitcode=expected,wall_seconds=time.monotonic()-began))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        assert run.returncode==expected,name
    cli('topology',['topology','--config','topology-input.json','--output','topology'])
    def run(name,source,expected=0):
        cli(name,['rdma-stream-probe','--topology','topology','--protocol','protocol.json',
                  '--config',source,'--backend',str(backend),'--output',name],expected)
        if not expected:
            usage=json.loads((out/name/'execution/probe.json').read_text())
            assert usage['status']=='completed' and usage['peak_sampled_group_rss_bytes']<=2**30
            return json.loads((out/name/'result.json').read_text())
    config=json.loads((out/'work.json').read_text());protocol=json.loads((out/'protocol.json').read_text())
    baseline=run('normal','work.json');normal=audit(baseline,config,protocol['payload_bytes'])
    rows={w['id']:w for w in baseline['persistent_transport']['work']}
    assert rows[1]['ready_ns']==rows[0]['accepted_ns']<rows[0]['acked_ns']==rows[1]['published_ns']
    assert next(s for s in baseline['persistent_transport']['streams'] if s['id']==0)['scheduler_reinsertions']>=2
    deep=deepcopy(config);deep['streams'][0]['queue_depth']=2
    (out/'deep.json').write_text(json.dumps(deep))
    deeper=run('deep','deep.json');deep_audit=audit(deeper,deep,protocol['payload_bytes'])
    rows={w['id']:w for w in deeper['persistent_transport']['work']}
    assert rows[1]['published_ns']==rows[1]['ready_ns']==rows[0]['accepted_ns']<rows[0]['acked_ns']
    concurrent=deepcopy(deep);concurrent['work'][1]['after']=[]
    (out/'concurrent.json').write_text(json.dumps(concurrent))
    concurrent_result=run('concurrent','concurrent.json')
    concurrent_audit=audit(concurrent_result,concurrent,protocol['payload_bytes'])
    rows={w['id']:w for w in concurrent_result['persistent_transport']['work']}
    assert rows[0]['published_ns']==rows[1]['published_ns']==0
    repeat=run('repeat','work.json')
    assert (out/'normal/result.json').read_bytes()==(out/'repeat/result.json').read_bytes()
    bad=deepcopy(config);bad['work'][0]['after']=[dict(work=5,event='accepted')]
    (out/'future.json').write_text(json.dumps(bad));run('future','future.json',1)
    assert not (out/'future').exists()
    wrong=deepcopy(config);wrong['streams'][0]['source_nic']=99
    (out/'wrong-nic.json').write_text(json.dumps(wrong));run('wrong-nic','wrong-nic.json',1)
    assert not (out/'wrong-nic/result.json').exists()
    failure=json.loads((out/'wrong-nic/execution/probe.json').read_text())
    assert failure['status']=='child_failed'
    assert 'unique declared source and reverse NIC routes' in (out/'wrong-nic/execution/stderr.log').read_text()
    total=sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    assert total<=32*2**20 and time.monotonic()-start<300
    source_paths=['backend/rdma/fabric.hh','backend/rdma/fabric.cc','backend/rdma/persistent_streams.hh','backend/rdma/persistent_streams.cc',
                  'backend/rdma/stream_probe.cc','backend/rdma/targets.cmake','backend/rdma/gin_work_queue.hh',
                  'python/moe_ub_cosim/scaleout_streams.py','python/moe_ub_cosim/scaleout_rdma.py','python/moe_ub_cosim/cli.py',
                  'tests/integration/check_persistent_stream_workflow.py','examples/scaleout_rdma_streams/work.json']
    verified=dict(passed=True,public_commands=len(commands),expected_rejections=2,
        normal=normal,depth2=deep_audit,concurrent=concurrent_audit,repeat_byte_identical=True,
        same_network_clock=True,hardware_calibrated=False,modern_GIN_executed=False,
        wall_seconds=time.monotonic()-start,output_bytes=total,
        backend_sha256=hashlib.sha256(backend.read_bytes()).hexdigest(),
        source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in source_paths})
    (out/'verification.json').write_text(json.dumps(verified,indent=2)+'\n');print(json.dumps(verified))


if __name__=='__main__':main()
