"""Offline acceptance, rejection sensitivity and public input regressions."""
import argparse
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve()
    spec=importlib.util.spec_from_file_location('limits_audit',repo/'fixtures/transaction_limits/audit.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    load=lambda p:json.loads(p.read_text())
    summaries={};conditions=[('expanded-corrected','expanded','network/expanded-corrected'),
        ('long-fault','long-recovery','network/long-fault-configured'),('online','expanded','network/online/api')]
    for label,work,directory in conditions:
        r=load(s/directory/'result.json');w=load(s/'inputs'/(work+'.json'))
        summaries[label]=module.audit(r,w)
    fault=load(s/'network/long-fault-configured/result.json')
    assert fault['fault_injection']['drop_counts']==[1]
    assert summaries['long-fault']['physically_transmitted_payload_bytes']>5120
    assert summaries['long-fault']['max_request_sequence']==63
    expanded=load(s/'network/expanded-corrected/result.json');work=load(s/'inputs/expanded.json')
    tampered=[]
    for case in ('wire-byte','target-page','queue-release','effective-count','copy-byte','physical-ACK'):
        bad=deepcopy(expanded)
        if case=='wire-byte':
            packet=bad['transactions']['transport']['source_payload_packets'][0]
            packet['hex']=packet['hex'][:-2]+('00' if packet['hex'][-2:]!='00' else 'ff')
        elif case=='target-page':
            page=bad['transactions']['windows'][0]['pages'][0];page['hex']='ff'+page['hex'][2:]
        elif case=='queue-release':bad['transactions']['connections'][0]['completed']-=1
        elif case=='effective-count':bad['transactions']['execution_limits']['max_connections']=8
        elif case=='copy-byte':
            event=next(e for e in bad['transactions']['local_memory_events'] if e['kind']=='captured')
            event['hex']='ff'+event['hex'][2:]
        else:
            c=bad['packet_capture'];fields=c['columns'];changed=0
            for e in c['events']:
                if e[fields.index('event')]==1 and e[fields.index('ip_protocol')]==252:
                    e[fields.index('time_ns')]+=1;changed+=1
            assert changed
        try:module.audit(bad,work)
        except AssertionError:tampered.append(case)
        else:raise AssertionError('audit accepted '+case)
    commands=[]
    for tail in ([sys.executable,'-B','-m','unittest','discover','-s','tests/unit','-p','*transaction*test.py'],
        [sys.executable,'-B',repo/'fixtures/transaction_limits/reject.py','--topology',s/'topology',
         '--protocol',s/'inputs/protocol.json','--work',s/'inputs/expanded.json',
         '--backend',s/'bin/transaction-probe','--output',s/'native-rejections']):
        done=subprocess.run(list(map(str,tail)),cwd=repo,timeout=20)
        commands.append(dict(command=list(map(str,tail)),cwd=str(repo),exit_code=done.returncode))
        assert done.returncode==0
    summary=dict(schema_version='transaction-limits-checks/1',conditions=summaries,
        tamper_classes_rejected=tampered,native_rejections=load(s/'native-rejections/verification.json')['native_pre_network_rejections'],
        online_candidate_atomicity=load(s/'network/online/verification.json'),commands=commands,
        functional_only=True,modern_gin_implemented=False,hardware_calibrated=False)
    (s/'verification.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summaries))


if __name__=='__main__':main()
