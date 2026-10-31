"""Read retained packet records; never launch, modify or complete a simulation."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--legacy-run',type=Path,required=True)
    p.add_argument('--atomic-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    repo=Path(__file__).resolve().parents[2]
    spec=importlib.util.spec_from_file_location('legacy_packet_audit',repo/'tests/integration/audit_legacy_native.py')
    audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
    load=lambda path:json.loads(path.read_bytes());sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    results=[];legacy=a.legacy_run.resolve();work=load(legacy/'execution.json')
    for name,path in [('legacy-failed',legacy/'result.json')]+[
        (name,a.atomic_root.resolve()/'cases'/name/'run/result.json') for name in ('normal','recovery','session-recovery')]:
        before=sha(path);result=load(path);assert result['gpu_count']==128
        net=result['legacy' if name=='legacy-failed' else 'transactions']['transport']
        if name=='legacy-failed':
            mapping=work['rank_to_gpu'];ep=work['ranks'];per_rank=work['experts']//ep;endpoints={};stream=0
            for src in range(ep):
                for dst in range(ep):
                    for _ in range(per_rank):
                        if src==dst:continue
                        endpoints[stream]=(mapping[src],mapping[dst],1,1)
                        endpoints[stream+1]=(mapping[dst],mapping[src],1,1);stream+=2
        else:
            endpoints={s['id']:(s['src'],s['dst'],s['source_nic'],s['destination_nic']) for s in net['streams']}
        _,_,_,summary=audit.verify_packet_bytes(net,result['packet_capture'],endpoints)
        if name!='legacy-failed':assert summary['unique_transmitted_payload_bytes']==summary['delivered_prefix_bytes']
        assert sha(path)==before
        results.append(dict(name=name,original_result_sha256=before,original_network_passed=result['passed'],
                            native_run_executed_now=False,packet_prefix_check=summary))
    evidence=dict(passed=True,new_network_executions=0,original_files_unchanged=True,
        complete_legacy_application_verified=False,source_sha256=sha(repo/'tests/integration/audit_legacy_native.py'),cases=results)
    (out/'verification.json').write_text(json.dumps(evidence,indent=2)+'\n');print(json.dumps(evidence))


if __name__=='__main__':main()
