"""Generated multi-plane network loaded by real UB, paths, queues and feedback."""
import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import yaml

from moe_ub_cosim.fabric import generate, physical
from moe_ub_cosim.observation import backend_observation


def read(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n');return p

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2];out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    base=read(root/'fixtures/m10_full_model/service.json');layers=read(root/'fixtures/m10_full_model/layers.json')
    spec=read(root/'fixtures/m10_fabric/small.json');defaults=read(root/'fixtures/m10_fabric/defaults.json')
    base['execution']['resource_budget']=read(root/'fixtures/m10_resources/graph-budget.json')
    fixture=root/'fixtures/m6_scenario_backend/reference.yaml';config=yaml.safe_load(fixture.read_text())
    config.update(schema_version='moe-ub-scenario-run/2',scenario_id='m10-fabric',max_simulation_ns=4000000)
    config['system']=str(root/'fixtures/m1_compute/system.json')
    config['backend']['reference_file']=str(root/'docs/scenarios/cm384_deepseek_r1_decode_v0.1.yaml')
    config['backend']['attributes']={}
    config['network']=dict(fabric_file='',defaults=defaults,nodes=[],links=[])
    topology,_,_,_=generate(spec,defaults)
    ranks=[r for g in spec['groups'] for r in g['rank_ids']]
    switches=[n for n in topology['nodes'] if n['kind']=='SWITCH']
    budget=read(root/'fixtures/m10_resources/observation-budget.json');budget['max_ports']=256
    config['observation']=dict(clocks=[dict(device_type=t,device_id=i,offset_ns=0,drift_ppb=0)
        for t,i in [('rank',r) for r in ranks]+[('switch',n['node_id']) for n in switches]],
        switches=[dict(switch_id=n['node_id'],port_ids=list(range(n['ports'])),start_local_ns=0,
                       end_local_ns=4000000,period_ns=20000) for n in switches],
        record_provenance=True,resource_budget=budget)
    commands=[];cases={};failures={}
    def invoke(label,argv,fail=False,stdin=None):
        argv=list(map(str,argv));start=datetime.now(timezone.utc).isoformat()
        meter="""import json,resource,subprocess,sys,time
start=time.monotonic();p=subprocess.run(sys.argv[2:])
with open(sys.argv[1],'w') as f:json.dump(dict(wall_seconds=time.monotonic()-start,max_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss),f)
sys.exit(p.returncode)
"""
        measured=[sys.executable,'-c',meter,str(out/f'{label}.time.json'),*argv]
        proc=subprocess.run(measured,cwd=root,input=stdin,capture_output=True,text=True,timeout=180)
        for key in ('stdout','stderr'):(out/f'{label}.{key}.log').write_text(getattr(proc,key))
        if stdin is not None:(out/f'{label}.stdin.log').write_text(stdin)
        commands.append(dict(label=label,argv=measured,cwd=str(root),started_utc=start,
            ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=proc.returncode,**read(out/f'{label}.time.json')))
        save(out/'commands.json',commands)
        assert (proc.returncode!=0 if fail else proc.returncode==0),(label,proc.stderr)
        return proc
    def prepared(label,f=None,c=None):
        f=deepcopy(spec if f is None else f);c=deepcopy(config if c is None else c);l=deepcopy(layers)
        for group,g in zip(l['groups'],f['groups']):
            for layer in group['layers']:
                layer['rank_to_ub_node']=g['rank_to_ub_node']
                layer['deployment']['ranks']=[physical(r,n,f['shape']) for r,n in zip(g['rank_ids'],g['rank_to_ub_node'])]
        save(out/f'{label}.research-observation.json',c['observation'])
        c['observation']=backend_observation(c['observation'],ranks,c['max_simulation_ns'])
        c['network']['fabric_file']=str(save(out/f'{label}.fabric.json',f))
        c['service']=str(save(out/f'{label}.service.json',base));c['layer']=str(save(out/f'{label}.layers.json',l))
        return c
    def command(label,c):
        p=out/f'{label}.yaml';p.write_text(yaml.safe_dump(c,sort_keys=False))
        return [sys.executable,'-m','moe_ub_cosim.scenario','run','--input',p,'--backend',binary,'--output-dir',out/label]
    def run(label,f=None,c=None):
        c=prepared(label,f,c);invoke(label,command(label,c));path=out/label
        front=read(path/'run/service_result.json');report=read(path/'run/execution/result.json')
        effective=read(path/'run/execution/effective_backend.json')
        assert report['normal_exit'] and report['network_drain']['idle'] and report['network_initializations']==1
        assert front['service_finished'] and len(front['waves'])==5
        assert effective['fabric_audit']['status']=='matched_before_business_activation'
        assert effective['fabric_audit']['route_queries']==1344 and effective['fabric_audit']['directed_transports']==56
        assert len(effective['topology']['nodes'])==47 and len(effective['topology']['links'])==126
        assert len(report['local_deliveries'])>0
        assert all(r[k]==0 for r in report['rank_resources'] for k in ('compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks'))
        assert all(r['kv_used_bytes']==0 and not r['request_blocks'] for r in front['memory_final'])
        assert all(not t['context_active'] for t in report['transfers'])
        assert all(not t['active'] and not t['pending_events'] for t in report['local_deliveries'])
        for t in report['transfers']:
            assert t['submitted_tasks']==t['send_callbacks']==t['receive_callbacks']==1
            assert t['bytes']==t['committed_bytes'] and t['data_ready_ns']<t['transaction_done_ns']==t['send_done_ns']
        cases[label]=dict(finished_ns=front['finished_ns'],writes=len(report['transfers']),
            local_deliveries=len(report['local_deliveries']),artifact_bytes=sum(p.stat().st_size for p in path.rglob('*') if p.is_file()))
        return path,front,report
    baseline,front,report=run('reference')
    c=deepcopy(config);c['observation']['record_provenance']=False
    off,_,offreport=run('source-off',c=c);assert report==offreport
    # Low bandwidth affects actual UB serialization/queues and subsequent service admission.
    c=deepcopy(config);c['network']['defaults']['rate_bps']=1000000000
    slow,sfront,sreport=run('slow',c=c)
    assert sfront['finished_ns']>front['finished_ns']
    later={w['graph_id']:w['submitted_ns'] for w in sfront['waves']}
    assert any(later[w['graph_id']]>w['submitted_ns'] for w in front['waves'])
    f=deepcopy(spec);f['groups'][0]['rank_to_ub_node']=[0,1,2];f['groups'][1]['rank_to_ub_node']=[4,5,6]
    local,_,localreport=run('same-server',f=f)
    def audit_paths(label,path,result,same_server=False):
        transfers={t['transfer_id']:t for t in result['transfers']};by_uid=defaultdict(list)
        planes=set();spines=set();waiting=0;counter_bytes=0;business_bytes=0
        local_ids={t['transfer_id'] for t in result['local_deliveries']}
        for port in (path/'run/execution/ports').iterdir():
            n,p=map(int,port.name.split('-'))
            events=[json.loads(x) for x in (port/'packet-events.jsonl').read_text().splitlines()]
            audit=read(port/'observation-audit.json')
            port_bytes=sum(e['counted_packet_bytes'] for e in events)
            assert port_bytes==audit['all_successful_start_bytes']==audit['native_tx_bytes_delta']
            assert len(events)==audit['all_successful_start_packets']
            counter_bytes+=port_bytes
            if label=='reference':
                other=off/'run/execution/ports'/port.name
                assert read(other/'observation-audit.json')['wire_and_time_fnv1a64']==audit['wire_and_time_fnv1a64']
                for name in ('packet-events.jsonl','egress-queue-audit.jsonl'):
                    assert (port/name).read_bytes()==(other/name).read_bytes()
            queue={}
            for line in (port/'egress-queue-audit.jsonl').read_text().splitlines():
                q=json.loads(line);uid=q['packet_uid']
                if q['event']=='enqueue':queue[uid]=q['time_ns']
                elif q['event']=='dequeue':waiting+=q['time_ns']-queue.pop(uid)
                else:assert q['event']=='tx_start'
            assert not queue
            for line in (port/'truth/packet_ranges.jsonl').read_text().splitlines():
                x=json.loads(line);assert x['transfer_id'] not in local_ids and x['transfer_id'] in transfers
                by_uid[x['packet_uid']].append((n,p,x));business_bytes+=x['packet_byte_range'][1]-x['packet_byte_range'][0]
        assert by_uid
        for visits in by_uid.values():
            visits.sort(key=lambda row:row[2]['sim_time_ns'])
            assert len(visits)==(1 if same_server else 3),visits
            destn,destp,_=visits[-1];plane=(destn-12)%7;planes.add(plane)
            t=transfers[visits[0][2]['transfer_id']]
            assert 12<=destn<33 and destp==t['dst_ub_node']%4 and (destn-12)//7==t['dst_ub_node']//4
            if not same_server:
                srcn,srcp,_=visits[0];mid,midp,_=visits[1]
                assert 12<=srcn<33 and (srcn-12)%7==plane and srcp in (4,5)
                assert mid==33+plane*2+srcp-4 and midp==(destn-12)//7
                assert (srcn-12)//7==t['src_ub_node']//4 and (srcn-12)//7!=(destn-12)//7
                spines.add(srcp-4)
        assert planes==set(range(7)) and (same_server or spines=={0,1})
        assert all(t['src_ub_node']//2!=t['dst_ub_node']//2 for t in result['transfers'])
        expected_bytes=sum(t['business_payload_bytes'] for p in (path/'run/graphs').glob('*/plan.json') for t in read(p)['transfers'] if 'ub_task_id' in t)
        assert business_bytes==expected_bytes*(1 if same_server else 3)
        assert business_bytes>0 and counter_bytes>business_bytes
        cases[label].update(observed_planes=sorted(planes),observed_l2_candidates=sorted(spines),
            queue_wait_ns=waiting,packet_paths=len(by_uid),switch_packet_bytes=counter_bytes,business_byte_visits=business_bytes)
        return waiting
    audit_paths('reference',baseline,report);assert audit_paths('slow',slow,sreport)>0
    audit_paths('same-server',local,localreport,True)
    # Invalid physical placement is rejected by the Python entry before backend creation.
    c=prepared('bad-placement');l=read(Path(c['layer']))
    for layer in l['groups'][0]['layers']:layer['deployment']['ranks'][0]['chip_id']='wrong'
    save(Path(c['layer']),l);p=invoke('bad-placement',command('bad-placement',c),True)
    assert 'Layer/fabric physical placement mismatch' in p.stderr
    assert not (out/'bad-placement/run').exists()
    failures['placement']='rejected before execution'
    # Bypass Python and mutate generated CSVs: actual objects must still be checked.
    def tamper(label,filename,edit,diagnostic):
        net=out/(label+'-network');shutil.copytree(baseline/'network',net)
        p=net/filename
        with p.open(newline='') as stream:rows=list(csv.reader(stream))
        edit(rows)
        with p.open('w',newline='') as stream:csv.writer(stream,lineterminator='\n').writerows(rows)
        proc=invoke(label,[binary,base['run_id'],baseline/'run',config['system'],net,'-',out/(label+'-execution'),4000000],True,stdin='')
        assert diagnostic in proc.stderr,(label,proc.stderr)
        assert not (out/(label+'-execution')/'result.json').exists()
        failures[label]=diagnostic
    tamper('missing-link','topology.csv',lambda r:r.pop(),'expected a connected UB link')
    tamper('wrong-plane-route','routing_table.csv',lambda r:r[1].__setitem__(3,'1'),'live shortest candidates differ')
    tamper('cross-plane-tp','transport_channel.csv',lambda r:r[1].__setitem__(4,'1'),'live TP endpoints/plane/metric differ')
    resolved=read(baseline/'network/backend.json')
    for label,edit,message in [
        ('bad-physical-link',lambda c:c['topology']['links'][0].__setitem__('dst_port',1),'device/L1 link differs'),
        ('bad-channel-budget',lambda c:c['fabric']['budget'].__setitem__('max_channels',1),'routing/channel budget'),
        ('bad-node-inventory',lambda c:c['topology']['nodes'].pop(),'topology size')]:
        c=deepcopy(resolved);edit(c)
        proc=invoke(label,[binary,'--check-backend',save(out/f'{label}.json',c)],True)
        assert message in proc.stderr;failures[label]=message
    net=out/'bad-mapping-network';shutil.copytree(baseline/'network',net)
    c=deepcopy(resolved);c['fabric']['groups'][0]['rank_to_ub_node']=[1,0,4];save(net/'backend.json',c)
    proc=invoke('bad-mapping',[binary,base['run_id'],baseline/'run',config['system'],net,'-',out/'bad-mapping-execution',4000000],True,stdin='')
    assert 'plan mapping differs' in proc.stderr and not (out/'bad-mapping-execution').exists()
    failures['bad-mapping']='service/fabric mapping rejected before network initialization'
    save(out/'summary.json',dict(passed=True,cases=cases,failures=failures,target_scale_executed=False))
    print('PASS: real multi-plane fabric, paths, queues, feedback, observation neutrality and live rejection')


if __name__=='__main__':main()
