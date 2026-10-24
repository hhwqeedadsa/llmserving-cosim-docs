"""Native pre-allocation rejects and independent evidence corruption checks."""
import argparse
from copy import deepcopy
from pathlib import Path
import subprocess
from fixtures.self_peer_service.stage import load,save
from fixtures.self_peer_service.audit import audit


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);a=p.parse_args();s=a.stage.resolve()
    cfg=load(s/'inputs/mixed.json');out=s/'reject';out.mkdir();rejected=[]
    changes={
        'missing_model':lambda c:c.pop('self_peer_model'),
        'unknown_model':lambda c:c['self_peer_model'].update(extra=1),
        'self_wire_port':lambda c:c['connections'][1].update(request_port=24002),
        'nonself_owner':lambda c:c['connections'][1].update(dst=0),
        'wrong_nic':lambda c:c['connections'][1].update(source_nic=3,destination_nic=3),
        'wrong_plane':lambda c:c['connections'][1].update(plane=1),
        'nic_mismatch':lambda c:c['connections'][1].update(destination_nic=2),
        'reversed_times':lambda c:c['connections'][1]['self_service'].update(apply_ns=4000),
        'boolean_time':lambda c:c['connections'][1]['self_service'].update(apply_ns=True),
        'over_deadline':lambda c:c['connections'][1]['self_service'].update(complete_ns=1000001),
        'fake_ack_dependency':lambda c:c['operations'][5].update(after=[dict(operation=3,event='request_acked')]),
        'atomic_permission':lambda c:c['windows'][1].update(allow_atomic=False),
    }
    commands=[]
    for label,change in changes.items():
        c=deepcopy(cfg);change(c);path=out/(label+'.json');result=out/(label+'-unexpected-result.json');save(path,c)
        cmd=list(map(str,[s/'runtime/run-transaction-probe',s/'topology/topology.json',s/'inputs/protocol.json',path,result]))
        with (out/(label+'.stdout')).open('w') as stdout,(out/(label+'.stderr')).open('w') as stderr:
            r=subprocess.run(cmd,cwd=s,stdout=stdout,stderr=stderr,timeout=3)
        commands.append(dict(command=cmd,cwd=str(s),exit_code=r.returncode));assert r.returncode==1 and not result.exists(),label
        rejected.append(label)
    save(out/'commands.json',commands)
    r=load(s/'mixed/result.json');tampered=[]
    alterations={
        'fake_self_ack':lambda r:r['transactions']['operations'][1].update(request_acked_ns=8000),
        'wrong_self_start':lambda r:r['transactions']['operations'][1].update(self_started_ns=0),
        'early_completion':lambda r:r['transactions']['operations'][1].update(completed_ns=1),
        'wrong_atomic_return':lambda r:r['transactions']['operations'][1].update(returned_value=100),
        'fabricated_self_stream':lambda r:r['transactions']['transport']['streams'].append(dict(id=2)),
        'duplicate_target':lambda r:r['transactions']['target_events'].append(deepcopy(r['transactions']['target_events'][0])),
        'wrong_final_memory':lambda r:r['transactions']['windows'][0]['pages'][0].update(hex='09'+r['transactions']['windows'][0]['pages'][0]['hex'][2:]),
        'corrupt_source_capture':lambda r:next(e for e in r['transactions']['source_events'] if e['kind']=='captured').update(hex='0900000000000000'),
    }
    for label,change in alterations.items():
        damaged=deepcopy(r);change(damaged)
        try:audit(damaged,cfg)
        except AssertionError:tampered.append(label)
        else:raise AssertionError('tampered evidence accepted: '+label)
    save(s/'checks.json',dict(passed=True,native_before_fabric_rejected=rejected,independent_oracle_tamper_rejected=tampered,
        native_allocation_basis='transactions/5 invokes ValidateSessionProgram and ValidateSelfServices before Fabric; native failure stderr retained',
        additional_network_executions=0))


if __name__=='__main__':main()
