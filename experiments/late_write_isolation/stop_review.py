"""Independent closeout of two stopped executions; no claim of late-write proof."""
import argparse
import hashlib
import json
from pathlib import Path
import admission_review


def read(p):return json.loads(p.read_text())
def check(ok,why):
    if not ok:raise AssertionError(why)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();s=a.stage.resolve()
    first=admission_review.audit(s);original=read(s/'manifest.json')
    for rel,digest in original['input_hashes'].items():check(hashlib.sha256((s/rel).read_bytes()).hexdigest()==digest,'input hash: '+rel)
    fast,late=(read(s/('inputs/'+name+'-topology.json')) for name in ('fast','late'))
    check(fast['nodes']==late['nodes'] and fast['nic_ports']==late['nic_ports'],'topology node/NIC change')
    changes=[(x,y) for x,y in zip(fast['links'],late['links']) if x!=y]
    check(len(changes)==1,'unexpected topology change');before,after=changes[0]
    check({k:v for k,v in before.items() if k!='delay_ns'}=={k:v for k,v in after.items() if k!='delay_ns'} and before['delay_ns']==1000 and after['delay_ns']==250000 and 0 in (after['src'],after['dst']),'only GPU0 delay changed')
    config=read(s/'inputs/protocol.json');switches={n['id'] for n in late['nodes'] if n['kind']!='gpu'}
    tables=[]
    for switch in sorted(switches):
        ports=[]
        for link in late['links']:
            if switch in (link['src'],link['dst']):
                port=link['src_port'] if switch==link['src'] else link['dst_port']
                headroom=(link['rate_bps']//1000000*link['delay_ns']//8000)*3
                ports.append(dict(port=port,headroom_bytes=headroom,link_id=link['id']))
        hdrm=sum(x['headroom_bytes'] for x in ports);reserve=len(ports)*4096;capacity=config['switch_buffer_bytes']
        tables.append(dict(node=switch,ports=ports,headroom_bytes=hdrm,reserve_bytes=reserve,buffer_bytes=capacity,admissible=hdrm+reserve<capacity))
    invalid=[x for x in tables if not x['admissible']];check(len(invalid)==1,'headroom constraint not reproduced')
    c=s/'cases/safe-late-isolated';terminal=read(c/'observed/terminal.json')
    check(terminal==dict(terminal=None,native_exit_code=1,policy_new_sent=False,policy_copy_sent=False),'native constructor terminal')
    check((c/'truth/native.stderr.log').read_text().strip()=='RDMA: headroom/reserve exceeds switch buffer','constructor error')
    check(not (c/'truth/result.json').exists() and not (c/'truth/control.jsonl').read_bytes() and not (c/'observed/decisions.jsonl').read_bytes(),'unexpected run/callback after constructor failure')
    check(not (s/'cases/safe-late-receive-order').exists(),'amended matrix continued')
    check({x.name for x in (s/'cases').iterdir()}=={'fast-shared','safe-late-isolated'},'extra cases')
    status=read(s/'gate-execution.json')+read(s/'safe-execution.json');check(len(status)==2 and all(x['status']=='child_failed' for x in status),'failed executions hidden')
    monitors=[]
    for q in sorted((s/'monitors').glob('*/probe.json')):
        report=read(q);check(report['status']=='child_failed' and report['child_exit_code']==1 and not report['remaining_live_group_pids'],'unexpected monitor status/liveness')
        monitors.append(dict(case=q.parent.name,status=report['status'],wall_seconds=report['wall_seconds']))
    output=dict(schema_version='late-write-stopped-study/1',first_case=first,second_case=dict(name='safe-late-isolated',status='native_construction_rejected',callbacks=0,source_packets=0,result_json_produced=False,invalid_switches=invalid),
        original_conditions_complete=False,amended_conditions_complete=False,stale_overwrite_proven=False,source_exhaustion_proven=False,
        safety_baseline_performance_evaluated=False,reason='experiment inputs omitted existing effect-order proof and exceeded fixed MMU headroom capacity; no platform protection weakened, no resource increase or network rerun',monitors=monitors,
        next_action='frontload joint topology/protocol capacity checks; redesign admissible boundary tests separately')
    a.output.write_text(json.dumps(output,sort_keys=True,indent=2)+'\n');print(json.dumps(dict(stopped_cases=2,invalid_switch=invalid[0]['node'],required_bytes=invalid[0]['headroom_bytes']+invalid[0]['reserve_bytes'],capacity_bytes=invalid[0]['buffer_bytes'])))


if __name__=='__main__':main()
