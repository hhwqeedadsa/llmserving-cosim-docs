"""Read-only global packet audit and complete per-group semantic projections."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .legacy_audit import audit as audit_group, verify_packet_bytes
from .scaleout_rdma import checked_json


def audit(work,result,protocol):
    assert work['schema_version']=='moe-legacy-shared-execution/1'
    assert result['passed'] and result['drained'] and result['legacy_shared']['finished']
    shared=result['legacy_shared'];net=shared['transport'];groups=work['groups'];reports=shared['groups']
    assert 1<=len(groups)<=2 and len(groups)==len(reports)
    assert net['closed'] and net['physical_drained'] and net['inflight_packets']==0
    assert net['recovery']['complete'] and not net['recovery']['failed'] and net['recovery']['omitted_events']==0
    assert result['packet_capture']['complete'] and result['packet_capture']['omitted_events']==0
    offsets=[];endpoints={};used=set();identities=set();offset=0;expected_work=0
    for group,report in zip(groups,reports):
        identity=group['id'];execution=group['execution'];mapping=execution['rank_to_gpu']
        assert identity not in identities;identities.add(identity)
        assert not used.intersection(mapping) and len(set(mapping))==len(mapping);used.update(mapping)
        assert report['id']==identity and report['stream_offset']==offset
        assert execution['transport_recovery']==work['transport_recovery']
        assert execution['dcqcn_reference']==work['dcqcn_reference']
        local_stream=0;ranks=execution['ranks'];experts=execution['experts']
        for s in range(ranks):
            for d in range(ranks):
                for _ in range(experts//ranks):
                    if s==d:continue
                    endpoints[offset+local_stream]=(mapping[s],mapping[d],1,1)
                    endpoints[offset+local_stream+1]=(mapping[d],mapping[s],1,1);local_stream+=2
        assert local_stream==execution['limits']['streams']
        offsets.append((offset,offset+local_stream));offset+=local_stream
        expected_work+=execution['limits']['work']
    assert len(net['streams'])==len(endpoints)==offset
    assert len(net['work'])==expected_work and len({row['id'] for row in net['work']})==expected_work
    assert all(row['stream'] in endpoints for row in net['work'])
    for stream in net['streams']:assert stream['wire_port']==20000+stream['id']
    packet_summary=verify_packet_bytes(net,result['packet_capture'],endpoints)[3]
    source_columns={name:i for i,name in enumerate(result['packet_capture']['columns'])}
    message=source_columns['message_id'];summaries=[]
    for group,report,(begin,end) in zip(groups,reports,offsets):
        owns=lambda stream:begin<=stream<end
        # Only read-side projections are rebased. Actual packet UID, wire_port,
        # time, source/destination, payload, global work ID and ordering survive.
        projected=dict(net)
        projected['streams']=[dict(row,id=row['id']-begin) for row in net['streams'] if owns(row['id'])]
        projected['work']=[dict(row,stream=row['stream']-begin) for row in net['work'] if owns(row['stream'])]
        projected['source_packets']=[[row[0],row[1]-begin,*row[2:]] for row in net['source_packets'] if owns(row[1])]
        projected['source_payload_packets']=[dict(row,stream=row['stream']-begin) for row in net['source_payload_packets'] if owns(row['stream'])]
        projected['selective_repeat']=dict(net['selective_repeat'],events=[dict(row,stream=row['stream']-begin)
            for row in net['selective_repeat']['events'] if owns(row['stream'])])
        capture=dict(result['packet_capture']);capture['events']=[]
        for row in result['packet_capture']['events']:
            if owns(row[message]):
                row=list(row);row[message]-=begin;capture['events'].append(row)
        group_result=dict(result,legacy=dict(report['legacy'],transport=projected),packet_capture=capture)
        group_protocol=deepcopy(protocol)
        if protocol.get('faults'):
            selected=[i for i,rule in enumerate(protocol['faults']) if owns(rule['message_id'])]
            group_protocol['faults']=[dict(protocol['faults'][i],message_id=protocol['faults'][i]['message_id']-begin) for i in selected]
            group_result['fault_injection']=dict(result['fault_injection'],drop_counts=[result['fault_injection']['drop_counts'][i] for i in selected])
        summary=audit_group(group['execution'],group_result,group_protocol)
        summary.update(id=group['id'],stream_offset=begin,group_physical_drain_measured=False)
        summaries.append(summary)
    assert sum(row['streams'] for row in summaries)==len(endpoints)
    assert sum(row['transmitted_payload_bytes'] for row in summaries)==packet_summary['transmitted_payload_bytes']
    return dict(passed=True,groups=summaries,shared_transport_packet_verification=packet_summary,
        global_carrier_work=expected_work,global_streams=len(endpoints),physical_finish_ns=result['finish_ns'],
        route_source_verified=False,hardware_calibrated=False,numerical_experts_executed=False,
        scope='Complete independent groups on one shared network; per-group times use the global clock and physical finish is global; no isolated-task counterfactual')


def run(directory,protocol,output):
    directory,protocol,output=map(lambda p:Path(p).resolve(),(directory,protocol,output))
    if output.exists() or not output.parent.is_dir():raise ValueError('Shared audit output must be new with an existing parent')
    summary=audit(checked_json(directory/'execution.json',64*2**20),
                  checked_json(directory/'result.json',256*2**20),checked_json(protocol,65536))
    summary.update(schema_version='moe-legacy-shared-audit/1',input_sha256={str(path):hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (directory/'execution.json',directory/'result.json',protocol)})
    output.write_text(json.dumps(summary,indent=2)+'\n');return summary
