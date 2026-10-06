"""Real native MoE execution, data-direction checks and passive observation."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import yaml


def read(p): return json.loads(p.read_text())
def rows(p): return [json.loads(line) for line in p.read_text().splitlines()]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2]
    out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    fixture=root/'fixtures/m11_native';binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    probe=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-plan-load-probe'
    base=yaml.safe_load((fixture/'scenario.yaml').read_text())
    for k in ('service','layer','system'):base[k]=str((fixture/base[k]).resolve())
    base['network']['case_path']=str((fixture/base['network']['case_path']).resolve())
    base['backend']['reference_file']=str((fixture/base['backend']['reference_file']).resolve())
    commands=[];summaries={};results={};fingerprints={}

    def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n');return path
    def invoke(label,argv,expected=0):
        argv=[str(x) for x in argv];start=datetime.now(timezone.utc).isoformat()
        p=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=180)
        for k in ('stdout','stderr'):(out/f'{label}.{k}.log').write_text(getattr(p,k))
        commands.append(dict(label=label,argv=argv,cwd=str(root),started_utc=start,
                             finished_utc=datetime.now(timezone.utc).isoformat(),exit_code=p.returncode))
        save(out/'commands.json',commands)
        assert p.returncode==expected,(label,p.returncode,p.stderr)
        return p

    for label,dispatch,combine,observed,truth,slots in (
        ('mixed','STORE','LOAD',True,True,8),('truth-off','STORE','LOAD',True,False,8),
        ('observation-off','STORE','LOAD',False,False,8),('store','STORE','STORE',True,True,8),
        ('load','LOAD','LOAD',True,True,8),('one-id','STORE','LOAD',True,True,1)):
        c=deepcopy(base);layer=read(Path(c['layer']))
        layer['native_profile'].update(dispatch=dispatch,combine=combine)
        c['layer']=str(save(out/f'{label}.layer.json',layer))
        c['backend']['profile_by_role']={k:'native_'+v.lower() for k,v in dict(dispatch=dispatch,combine=combine).items()}
        c['backend']['attributes']['ns3::UbLdstApi::RequestIdCount']=slots
        if not observed:c['observation']=None
        else:
            for port in c['observation']['ports']:port['record_provenance']=truth
        path=out/f'{label}.yaml';path.write_text(yaml.safe_dump(c,sort_keys=False))
        invoke(label,[sys.executable,'-m','moe_ub_cosim.scenario','run','--input',path,'--backend',binary,'--output-dir',out/label])
        execution=out/label/'run/execution';r=read(execution/'result.json');front=read(out/label/'run/service_result.json')
        assert r['normal_exit'] and front['service_finished'] and r['network_initializations']==1
        assert r['network_drain']['idle'] and r['network_drain']['started_packets']==r['network_drain']['arrived_packets']
        assert not r['wqe_starts'] and not r['wqe_completions']
        assert len(r['native_starts'])==len(r['native_completions'])==len(r['transfers'])
        assert all(x[k]==0 for x in r['rank_resources'] for k in ('compute_slots','send_slots','pending_events','pending_tasks','stale_callbacks'))
        effective=read(execution/'effective_backend.json')
        assert effective['attributes']['ns3::UbSwitch::FlowControl']=='CBFC'
        actual={t['transfer_id']:t for t in r['transfers']};totals=Counter();loads=0;notices=0
        for wave in front['waves']:
            p=read(out/label/'run/graphs'/wave['graph_id']/'plan.json')
            events={(e['rank_id'],e['node_id']):e for e in r['node_execution'] if e['graph_id']==p['graph_id']}
            for g in p['rank_graphs']:
                for n in g['nodes']:
                    e=events[g['rank_id'],n['node_id']]
                    assert all(e['start_ns']>=events[g['rank_id'],d]['end_ns'] for d in n['deps'])
                    if n['kind']=='COMP':assert e['end_ns']-e['start_ns']==n['duration_ns']
            for t in p['transfers']:
                if 'ub_task_id' not in t:continue
                a=actual[t['transfer_id']]
                assert a['bytes']==a['committed_bytes']==t['payload_bytes']
                assert a['send_callbacks']==a['receive_callbacks']==a['submitted_tasks']==1
                assert a['initiator_resources_retired'] and not a['context_active']
                assert a['send_done_ns']==a['transaction_done_ns']
                assert a['receive_done_ns']==max(a['data_ready_ns'],a['receive_start_ns'])
                assert events[t['src_rank'],t['send_node_id']]['end_ns']==a['send_done_ns']
                assert events[t['dst_rank'],t['recv_node_id']]['end_ns']==a['receive_done_ns']
                if t['operation']=='LOAD':
                    loads+=1
                    assert a['data_ready_ns']==a['send_done_ns']
                    assert (a['data_owner_rank'],a['consumer_rank'])==(t['dst_rank'],t['src_rank'])
                    notice=next(n for n in p['transfers'] if n['message_role']=='ready' and
                                n['ready_for_message_id']==t['message_id'] and n['chunk_index']==t['chunk_index'])
                    assert a['send_start_ns']>=actual[notice['transfer_id']]['receive_done_ns']
                else:assert a['data_ready_ns']<a['send_done_ns']
                if t['message_role']=='ready':
                    notices+=1;assert t['business_payload_bytes']==0 and t['application_metadata_bytes']==32
                totals.update({k:t[k] for k in ('business_payload_bytes','application_metadata_bytes','padding_bytes')})
        assert loads==notices
        results[label]=r['transfers']
        if observed:
            fingerprints[label]=[read(execution/f'ports/{n}-4/observation-audit.json') for n in (8,9)]
            if truth:
                byte_rows=[row for n in (8,9) for row in rows(execution/f'ports/{n}-4/truth/port_byte_truth.jsonl')]
                assert sum(row['business_payload_bytes'] for row in byte_rows)==totals['business_payload_bytes']
                assert sum(row['application_metadata_bytes'] for row in byte_rows)==totals['application_metadata_bytes']
                assert sum(row['padding_bytes'] for row in byte_rows)>=totals['padding_bytes']
                for n in (8,9):
                    for row in rows(execution/f'ports/{n}-4/truth/packet_ranges.jsonl'):
                        lo,hi=row['packet_byte_range'];assert 0<=lo<hi<=row['counted_packet_bytes']
        summaries[label]=dict(simulation_end_ns=r['simulation_end_ns'],transfers=len(actual),loads=loads,
                              ready_notifications=notices,physical_packets=r['network_drain']['started_packets'],**totals)
    assert results['mixed']==results['truth-off']==results['observation-off']
    assert fingerprints['mixed']==fingerprints['truth-off']
    assert summaries['one-id']['simulation_end_ns']>summaries['mixed']['simulation_end_ns']
    resolved=read(out/'mixed/network/backend.json')
    rejected_attributes={
        'zero-threads':('ns3::UbLdstInstance::ThreadNum','0'),
        'zero-store-credit':('ns3::UbLdstThread::StoreOutstanding','0'),
        'zero-request-ids':('ns3::UbLdstApi::RequestIdCount','0'),
        'oversize-request-ids':('ns3::UbLdstApi::RequestIdCount','65537'),
        'rounded-load-size':('ns3::UbLdstThread::LoadResponseSize','65'),
        'oversize-store':('ns3::UbLdstThread::StoreRequestSize','16384')}
    for label,(key,value) in rejected_attributes.items():
        c=deepcopy(resolved);c['attributes'][key]=value
        invoke(label,[binary,'--check-backend',save(out/f'{label}.json',c)],1)
    c=deepcopy(base);c['backend']['profile_by_role']['combine']='native_store'
    path=out/'mismatched-profile.yaml';path.write_text(yaml.safe_dump(c))
    invoke('mismatched-profile',[sys.executable,'-m','moe_ub_cosim.scenario','run',
        '--input',path,'--backend',binary,'--output-dir',out/'mismatched-profile'],1)
    graph=out/'mixed/run/graphs/g0'
    invoke('cpp-plan-valid',[probe,graph])
    for label in ('early-load','wrong-owner','missing-notice','negative-notice-id','floating-notice-id'):
        directory=out/label;shutil.copytree(graph,directory)
        p=read(directory/'plan.json');load=next(t for t in p['transfers'] if t.get('operation')=='LOAD')
        if label=='wrong-owner':load['data_owner_rank']=load['src_rank']
        elif label=='missing-notice':p['transfers']=[t for t in p['transfers'] if t['message_role']!='ready']
        elif label in ('negative-notice-id','floating-notice-id'):
            notice=next(t for t in p['transfers'] if t['message_role']=='ready')
            notice['transfer_id']=-1 if label=='negative-notice-id' else 1.5
        else:
            node=next(n for g in p['rank_graphs'] if g['rank_id']==load['src_rank'] for n in g['nodes'] if n['node_id']==load['send_node_id'])
            node['deps']=[]
        save(directory/'plan.json',p);invoke(label,[probe,directory],1)
    save(out/'summary.json',dict(passed=True,cases=summaries,passive_wire_and_time_equal=True,
                               rejected_cpp_plans=5,rejected_backend_attributes=len(rejected_attributes),
                               mismatched_profile_rejected=True,calibration_status='unvalidated_reference'))
    print('PASS native MoE: ownership, real readiness messages, completion, exact useful bytes, resource response and passive observation')


if __name__=='__main__':main()
