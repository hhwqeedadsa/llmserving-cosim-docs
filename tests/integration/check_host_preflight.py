"""Actual multi-round UB observation footprint; counts are never extrapolated RSS."""
import argparse
import csv
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.host_preflight import add_token,empty,usage
from moe_ub_cosim.resources import actual_usage
from check_shared_bottleneck import audit_queue


def read(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n');return p
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    a=parser.parse_args();root=Path(__file__).resolve().parents[2];out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    c=read(root/'fixtures/m10_full_model/service.json');c['groups']=c['groups'][:1];c['requests']=c['requests'][:1]
    c['requests'][0]['decode_routes']=[{str(l):[4,5] for l in (1,2,3)} for _ in range(2)]
    c['execution']['resource_budget']=read(root/'fixtures/m10_resources/graph-budget.json')
    layers=read(root/'fixtures/m10_full_model/layers.json');layers['groups']=layers['groups'][:1]
    for layer in layers['groups'][0]['layers']:layer['chunk_bytes']=16
    cp=save(out/'service.json',c);lp=save(out/'layers.json',layers);commands=[];cases={};reference=None
    ports_all=[(s,p) for s in (8,9) for p in range(5)];selections=[('one',[(8,4)],True),('two',[(8,4),(9,4)],True),
        ('ten',ports_all,True),('ten-source-off',ports_all,False)]
    budget=read(root/'fixtures/m10_resources/observation-budget.json');max_ns=2000000
    rates={}
    with (root/'fixtures/m3_shared_bottleneck/network/topology.csv').open() as stream:
        for link in csv.DictReader(stream):
            assert link['bandwidth'].endswith('Gbps')
            for side in ('1','2'):rates[int(link['nodeId'+side]),int(link['portId'+side])]=int(link['bandwidth'][:-4])
    for label,ports,provenance in selections:
        obs=dict(schema_version='moe-ub-port-observation-set/2',resource_budget=budget,ports=[
            dict(schema_version='moe-ub-port-observation/2',switch_id=s,port_id=p,window_start_ns=0,window_end_ns=max_ns,
                 window_period_ns=1000,record_provenance=provenance,resource_budget=budget) for s,p in ports])
        op=save(out/(label+'.observation.json'),obs);destination=out/label
        argv=[sys.executable,'-m','moe_ub_cosim.host_probe','--output-dir',str(destination),
            '--rss-bytes',str(8*2**30),'--address-bytes',str(12*2**30),'--artifact-bytes',str(10*2**30),'--wall-seconds','300','--',
            sys.executable,'-m','moe_ub_cosim.service','--input',str(cp),'--layer',str(lp),
            '--backend',str(a.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'),
            '--system',str(root/'fixtures/m1_compute/system.json'),'--network',str(root/'fixtures/m3_shared_bottleneck/network'),
            '--output-dir',str(destination/'run'),'--max-simulation-ns',str(max_ns),'--observation',str(op)]
        started=datetime.now(timezone.utc).isoformat();p=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=320)
        save(out/'commands.json',commands+[dict(argv=argv,cwd=str(root),started_utc=started,ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=p.returncode)])
        commands=read(out/'commands.json');(out/(label+'.stdout.log')).write_text(p.stdout);(out/(label+'.stderr.log')).write_text(p.stderr)
        assert p.returncode==0,(label,p.stderr)
        run=destination/'run';result=read(run/'execution/result.json');front=read(run/'service_result.json');profile=read(destination/'probe.json')
        assert result['normal_exit'] and result['network_initializations']==1 and result['network_drain']['idle']
        assert len(front['waves'])==2 and front['waves'][1]['submitted_ns']==front['waves'][0]['finished_ns']
        assert len(result['transfers'])==2400 and len(result['local_deliveries'])==24
        assert sum(t['business_payload_bytes'] for wave in front['waves'] for t in read(run/f"graphs/{wave['graph_id']}/plan.json")['transfers'] if 'ub_task_id' in t)==36864
        assert all(r[k]==0 for r in result['rank_resources'] for k in ('compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks'))
        assert all(r['kv_used_bytes']==0 for r in front['memory_final'])
        counted_total=dict(transfers=0,fabric_transfers=0,local_transfers=0,nodes=0)
        for wave in front['waves']:
            plan=read(run/f"graphs/{wave['graph_id']}/plan.json");counted={k:0 for k in counted_total}
            for layer in plan['layers']:
                inp=layer['input'];state=empty();chips={r['rank_id']:r['chip_id'] for r in inp['deployment']['ranks']};positions={r:i for i,r in enumerate(inp['rank_ids'])}
                for token in inp['tokens']:add_token(state,token['src_rank'],token['expert_ids'],inp['expert_placement'],chips,positions)
                counts=usage(inp,state)
                for key in counted:counted[key]+=counts[key]
            counted['nodes']+=4+8+3*10
            assert counted==actual_usage(plan)==dict(transfers=1212,fabric_transfers=1200,local_transfers=12,nodes=2487)
            for key in counted:counted_total[key]+=counted[key]
        if reference is None:reference=result
        else:assert result==reference  # Observed port count and attribution do not alter execution.
        audits=[]
        for s,port in ports:
            directory=run/f'execution/ports/{s}-{port}';r=read(directory/'resource-audit.json');packet=read(directory/'observation-audit.json')
            assert r['registered_transfers']==2400 and r['ledger_packets']==2400
            assert r['retained_packet_event_records']==r['retained_queue_records']==r['retained_packet_range_records']==0
            assert r['logical_window_rows']==2000
            if packet['all_successful_start_packets']:
                audit_queue(directory,bandwidth_gbps=rates[s,port],switch_id=s,port_id=port,native_directory=run/'execution/network/runlog')
            else:
                # Native trace files are created on an event, not for idle ports.
                # The observer's explicit hardware counter snapshot proves zero traffic.
                assert packet['native_tx_bytes_delta']==packet['all_successful_start_bytes']==r['resident_window_rows']==0
                assert (directory/'packet-events.jsonl').read_bytes()==(directory/'egress-queue-audit.jsonl').read_bytes()==b''
            audits.append(dict(switch=s,port=port,**r,actual_egress_packets=packet['all_successful_start_packets']))
        shared=run/'execution/provenance-audit.json'
        if shared.exists():
            storage=read(shared)
            assert storage['storage']=='shared_run_ledger' and storage['ledger_instances']==1
            assert storage['descriptor_tables']==storage['packet_tables']==1
            assert storage['descriptor_records']==storage['packet_records']==storage['created_notifications']==2400
            assert storage['port_observers']==len(ports)
            assert all(r['provenance_storage']=='shared_run_ledger' and
                       r['owned_descriptor_records']==r['owned_packet_provenance_records']==0 for r in audits)
        else:
            # Historical backend: independently audited per-port ownership in
            # fixtures/m10_host_preflight. Logical counters alone do not prove sharing.
            storage=dict(storage='historical_per_port_ledgers',descriptor_tables=2*len(ports),packet_tables=len(ports),
                         descriptor_records=2*len(ports)*2400,packet_records=len(ports)*2400)
        cases[label]=dict(profile=profile,finished_ns=front['finished_ns'],counts=counted_total,
            ports=audits,provenance_storage=storage)
        print(label,'ports',len(ports),'RSS',profile['peak_sampled_group_rss_bytes'],'completed',front['finished_ns'],flush=True)
    for s,p in ports_all:
        on=out/f'ten/run/execution/ports/{s}-{p}';off=out/f'ten-source-off/run/execution/ports/{s}-{p}'
        assert read(on/'observation-audit.json')==read(off/'observation-audit.json')
        for name in ('packet-events.jsonl','egress-queue-audit.jsonl'):
            assert sha(on/name)==sha(off/name)
    save(out/'summary.json',dict(passed=True,cases=cases,target_scale_executed=False,
        result='All ports see all logical writes and packet UIDs; physical shared or historical per-port ownership is reported separately'))


if __name__=='__main__':main()
