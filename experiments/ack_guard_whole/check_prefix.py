"""Reject corrupted saved evidence without rerunning a network."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import tempfile

from guard_prefix import prefix

def load(p):return json.loads(p.read_bytes())

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--frozen',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();frozen=load(a.frozen);checked=[]
    for condition in ('normal','local'):
        path=Path(frozen['new_cases']['guarded-'+condition]['output'])
        c,r,f=(load(path/name) for name in ('input.json','result.json','frontend.json'))
        prefix(c,r,f,path)
        kinds=['timer','deadline','missing_post','unknown_post','report_ack','drop_decision','first_step_result','step_count']
        for kind in kinds:
            cc,rr,ff=deepcopy(c),deepcopy(r),deepcopy(f)
            lines=(path/'native.stdout.log').read_text().splitlines(keepends=True)
            if kind=='timer':rr['service']['control_wakeup']['events'][0]['deadline_ns']+=1
            elif kind=='deadline':cc['whole_call_replication']['calls'][0]['source_ack_timeout_ns']+=1000
            elif kind in ('missing_post','unknown_post'):
                index=next(i for i,line in enumerate(lines) if line.startswith('MOE_RDMA_EVENT ') and
                    (e:=json.loads(line[len('MOE_RDMA_EVENT '):])).get('kind')=='message_send_post' and e.get('graph_id')=='g0.w0')
                if kind=='missing_post':lines.pop(index)
                else:
                    e=json.loads(lines[index][len('MOE_RDMA_EVENT '):]);e['message']=2147483647
                    lines[index]='MOE_RDMA_EVENT '+json.dumps(e)+'\n'
            elif kind=='report_ack':
                messages=ff['whole_call_replication']['source_ack_guard']['messages'];next(iter(messages.values()))['ack_ns']+=1
            elif kind=='drop_decision':ff['whole_call_replication']['source_ack_guard']['decisions'].pop(0)
            elif kind=='first_step_result':
                call=ff['whole_call_replication']['calls']['target'];call['result_ready_ns']=call['attempts']['primary']['waves'][0]['finished_ns']
            else:
                # Independent prefix covers step lifetimes, while the separate
                # inspector verifies KV. Do not pretend it checks KV itself.
                cc['workload']['decode_steps']=1
            with tempfile.TemporaryDirectory() as tmp:
                shadow=Path(tmp);(shadow/'graphs').symlink_to(path/'graphs',target_is_directory=True)
                (shadow/'native.stdout.log').write_text(''.join(lines))
                try:prefix(cc,rr,ff,shadow)
                except (AssertionError,KeyError,StopIteration,ValueError):checked.append(dict(condition=condition,mutation=kind,rejected=True))
                else:raise AssertionError('corruption accepted: '+condition+'/'+kind)
    with a.output.open('x') as stream:json.dump(dict(passed=True,negative_cases=checked,new_network_executions=0),stream,indent=2);stream.write('\n')
    print(json.dumps(dict(passed=True,rejected=len(checked),new_network_executions=0)))

if __name__=='__main__':main()
