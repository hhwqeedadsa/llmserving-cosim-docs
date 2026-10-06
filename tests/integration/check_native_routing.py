"""Native physical routes, spray, unequal paths, useful data and passive capture."""
import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import yaml

from moe_ub_cosim.fabric import generate


def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n');return p


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2]
    out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    fixture=root/'fixtures/m12_native_routing';base=yaml.safe_load((fixture/'scenario.yaml').read_text())
    for key in ('service','layer','system'):base[key]=str((fixture/base[key]).resolve())
    base['backend']['reference_file']=str((fixture/base['backend']['reference_file']).resolve())
    base['network']['fabric_file']=str((fixture/base['network']['fabric_file']).resolve())
    spec=read(Path(base['network']['fabric_file']))
    binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    runtime=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-native-routing-test'
    commands=[];summaries={};runs={}

    def invoke(label,argv,code=0):
        argv=list(map(str,argv));start=datetime.now(timezone.utc).isoformat()
        p=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=180)
        for stream in ('stdout','stderr'):(out/f'{label}.{stream}.log').write_text(getattr(p,stream))
        commands.append(dict(label=label,argv=argv,cwd=str(root),started_utc=start,
                             ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=p.returncode))
        save(out/'commands.json',commands);assert p.returncode==code,(label,p.returncode,p.stderr)

    def audit_routes(r):
        by_uid=defaultdict(list);sources=defaultdict(set);spines=defaultdict(set)
        for hop in r['paths']:by_uid[hop['uid']].append(hop)
        assert set(by_uid)=={c['uid'] for c in r['created']}
        for uid,hops in by_uid.items():
            hops.sort(key=lambda x:x['time_ns']);first=hops[0]
            src,dst=first['src'],first['dst'];plane=first['port']
            assert len(hops)==4 and first['node']==src and plane in (0,1)
            assert all((h['src'],h['dst'],bool(h['response']))==(src,dst,bool(first['response'])) for h in hops)
            assert all(bool(h['spray'])==r['packet_spray'] for h in hops)
            assert hops[1]['node']==4+src//2*2+plane and hops[1]['port'] in (2,3,4)
            spine=hops[1]['port']-2
            assert (hops[2]['node'],hops[2]['port'])==(8+plane*3+spine,dst//2)
            assert (hops[3]['node'],hops[3]['port'])==(4+dst//2*2+plane,dst%2)
            sources[bool(first['response'])].add(plane)
            spines[bool(first['response']),plane].add(spine)
        for response in (False,True):
            assert len(sources[response])==(2 if r['packet_spray'] else 1)
            selected=[v for (reply,p),v in spines.items() if reply==response]
            assert all(len(v)>1 if r['packet_spray'] else len(v)==1 for v in selected)
        order={c['uid']:i for i,c in enumerate(r['created'])};highest={};inversions=defaultdict(int)
        for arrival in r['data_arrivals']:
            task=arrival['task'];i=order[arrival['uid']]
            inversions[task]+=i<highest.get(task,-1);highest[task]=max(i,highest.get(task,-1))
        assert {c['task'] for c in r['completions']}=={100,101} and len(r['completions'])==2
        for c in r['completions']:
            arrivals=[a for a in r['data_arrivals'] if a['task']==c['task']]
            assert c['bytes']==sum(a['bytes'] for a in arrivals)==65537
            assert c['ready_ns']==max(a['time_ns'] for a in arrivals)<=c['done_ns']
        return dict(source_ports={str(k):sorted(v) for k,v in sources.items()},
            spines={f'{reply}:{plane}':sorted(v) for (reply,plane),v in spines.items()},
            out_of_order_data_arrivals=dict(inversions),packets_with_paths=len(by_uid))

    for label,spray,delayed,truth in (
        ('fixed',False,False,True),('spray',True,False,True),
        ('unequal',True,True,True),('truth-off',True,True,False)):
        c=deepcopy(base);c['backend']['attributes']['ns3::UbLdstApi::UsePacketSpray']=spray
        if delayed:
            topology,_,_,_=generate(spec,c['network']['defaults'])
            c['network']['links']=[dict(link,delay_ns=2000) for link in topology['links'] if link['src']>=4 and link['src_port']==4]
        for p in c['observation']['ports']:p['record_provenance']=truth
        path=out/f'{label}.yaml';path.write_text(yaml.safe_dump(c,sort_keys=False))
        invoke(label,[sys.executable,'-m','moe_ub_cosim.scenario','run','--input',path,'--backend',binary,'--output-dir',out/label])
        execution=out/label/'run/execution';report=read(execution/'result.json');front=read(out/label/'run/service_result.json')
        effective=read(execution/'effective_backend.json')
        assert report['normal_exit'] and front['service_finished'] and report['network_drain']['idle']
        assert effective['fabric_audit']['native_memory']['source_ports']==[0,1]
        assert effective['fabric_audit']['native_memory']['remote_l1_uplinks']==[2,3,4]
        assert effective['attributes']['ns3::UbSwitch::FlowControl']=='CBFC'
        native_objects=[o for o in effective['objects'] if o['role']=='native_api']
        assert native_objects and all(o['attributes']['ns3::UbLdstApi::UsePacketSpray']==str(spray).lower() for o in native_objects)
        completed={t['transfer_id']:t for t in report['transfers']};business=metadata=0
        for wave in front['waves']:
            p=read(out/label/'run/graphs'/wave['graph_id']/'plan.json')
            events={(e['rank_id'],e['node_id']):e for e in report['node_execution'] if e['graph_id']==p['graph_id']}
            for graph in p['rank_graphs']:
                for node in graph['nodes']:
                    event=events[graph['rank_id'],node['node_id']]
                    assert all(event['start_ns']>=events[graph['rank_id'],d]['end_ns'] for d in node['deps'])
            for t in p['transfers']:
                if 'ub_task_id' not in t:continue
                x=completed[t['transfer_id']]
                business+=t['business_payload_bytes'];metadata+=t['application_metadata_bytes']
                assert x['bytes']==x['committed_bytes']==t['payload_bytes']
                assert x['submitted_tasks']==x['send_callbacks']==x['receive_callbacks']==1
                assert x['initiator_resources_retired'] and not x['context_active']
        if truth:
            rows=[json.loads(line) for directory in (execution/'ports').iterdir()
                  for line in (directory/'truth/port_byte_truth.jsonl').read_text().splitlines()]
            # All nonlocal messages cross servers in this one-chip-per-server
            # fixture, hence exactly three observed switch egress hops.
            assert sum(r['business_payload_bytes'] for r in rows)==3*business
            assert sum(r['application_metadata_bytes'] for r in rows)==3*metadata
        invoke(label+'-runtime',[runtime,out/label/'network',out/(label+'-runtime'),0,2,int(truth)])
        r=read(out/(label+'-runtime')/'result.json');assert r['passed'] and r['no_tp_created']
        runs[label]=(report,r)
        summaries[label]=dict(simulation_end_ns=report['simulation_end_ns'],native_transfers=len(completed),
            runtime_physical_packets=r['physical_starts'],routes=audit_routes(r) if truth else 'detail_disabled')
    assert all(v==0 for v in summaries['fixed']['routes']['out_of_order_data_arrivals'].values())
    assert all(v>0 for v in summaries['unequal']['routes']['out_of_order_data_arrivals'].values())
    assert runs['unequal'][0]==runs['truth-off'][0]
    for k in ('created','data_arrivals','completions','physical_starts','physical_arrivals','wire_and_time_hash'):
        assert runs['unequal'][1][k]==runs['truth-off'][1][k],k
    for p in (out/'unequal/run/execution/ports').iterdir():
        assert read(p/'observation-audit.json')==read(out/'truth-off/run/execution/ports'/p.name/'observation-audit.json')
    changed=read(out/'spray/network/backend.json')
    changed['attributes']['ns3::UbLdstApi::UseShortestPaths']='false'
    invoke('reject-nonshortest',[binary,'--check-backend',save(out/'nonshortest.json',changed)],1)
    save(out/'summary.json',dict(passed=True,cases=summaries,passive_wire_and_time_equal=True,
        nonshortest_rejected=True,hardware_calibrated=False,multiple_tp_enabled=False))
    print('PASS native spray: actual source ports, in-plane switch paths, response direction, reordering and passive capture')


if __name__=='__main__':main()
