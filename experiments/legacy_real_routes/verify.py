"""Independent source and byte-mask oracle for the post-run delivery curves."""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import sys


def check_curve(rows,expected):
    increments=Counter();previous_time=previous_total=0
    for time,size,total in rows:
        assert time>=previous_time and size>=0 and total==previous_total+size
        if size:increments[time]+=size
        previous_time,previous_total=time,total
    assert increments==expected
    assert previous_total==sum(expected.values())


def main():
    if sys.flags.optimize:
        raise RuntimeError('Verification requires assertions; do not use Python -O')
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--delivery',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    root=a.root.resolve();study=root/'study';delivery=a.delivery.resolve();out=a.output.resolve()
    assert not out.exists();out.mkdir(parents=True)
    load=lambda path:json.loads(Path(path).read_bytes());sha=lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()
    frozen=load(study/'frozen-v2.json');analysis=load(root/'analysis/analysis.json');curves=load(delivery/'delivery.json')
    assert sha(Path(__file__).parent/'study.py')==frozen['study_source_sha256']
    assert sha(Path(__file__).parent/'publish.py')==curves['postrun_source_sha256']
    assert sha(root/'analysis/analysis.json')==curves['analysis_sha256']
    source=load(frozen['source_manifest']);tokens=load(study/'plan-input.json')['calls'][0]['tokens']
    assert sha(frozen['source_manifest'])==frozen['source_manifest_sha256']
    counts=Counter()
    for rank,row in enumerate(source['files']):
        path=Path(row['file']['path']);assert sha(path)==row['file']['sha256']
        selected=load(path)[1]['3'][0];assert tokens[rank]==[selected];counts.update(selected)
    assert dict(sorted((str(k),v) for k,v in counts.items()))==analysis['expert_counts']
    assert all(not rank for rank in tokens[15:]) and sum(counts.values())==120
    assert len({row['file']['sha256'] for row in source['files']})==15
    witnesses={};final_ids=set();messages={};rows_by_case={};tamper_checks=0
    for case in ('normal','local','prefix'):
        run=study/('run-'+case);assert sha(run/'result.json')==analysis['receipts'][case]['raw_result_sha256']
        result=load(run/'result.json');work=load(run/'execution.json');legacy=result['legacy'];net=legacy['transport']
        ledger=defaultdict(list)
        for item in net['work']:ledger[item['stream']].append(item)
        for entries in ledger.values():entries.sort(key=lambda item:item['begin'])
        masks={};expected_bytes=0
        for op,row in zip(work['operations'],legacy['operations']):
            if op[2:4]!=[0,'data'] or op[4]==op[5]:continue
            stream=2*legacy['contexts'][row[0]][3];entry=ledger[stream][row[1]]
            assert entry['end']-entry['begin']==32+op[8]
            mask=masks.setdefault(stream,bytearray(ledger[stream][-1]['end']))
            begin=entry['begin']+48;end=entry['end'];assert end-begin==2*work['hidden_size']
            assert not any(mask[begin:end]);mask[begin:end]=b'\x01'*(end-begin);expected_bytes+=end-begin
        expected=Counter()
        for event in net['selective_repeat']['events']:
            if event['kind']!='payload_delivered' or event['stream'] not in masks:continue
            start=event['sequence'];size=len(event['hex'])//2
            count=sum(masks[event['stream']][start:start+size])
            if count:expected[event['time_ns']]+=count
        assert sum(expected.values())==expected_bytes==1677312
        path=delivery/(case+'-dispatch.csv');assert sha(path)==curves['curves'][case]['csv_sha256']
        with path.open() as stream:rows=[tuple(map(int,row)) for row in list(csv.reader(stream))[1:]]
        check_curve(rows,expected)
        assert rows[-1][0]==curves['details'][case]['last_effective_contiguous_delivery_ns']
        if case=='normal':
            index=next(i for i,row in enumerate(rows) if row[1])
            for mode in ('time','increment','missing','cumulative'):
                bad=deepcopy(rows);time,size,total=bad[index]
                if mode=='time':bad[index]=(time+1,size,total)
                elif mode=='increment':bad[index]=(time,size+1,total)
                elif mode=='missing':bad.pop(index)
                else:bad[index]=(time,size,total+1)
                try:check_curve(bad,expected)
                except AssertionError:tamper_checks+=1
                else:raise AssertionError('Curve tampering accepted: '+mode)
        times=legacy['event_times_ns'];events=work['events']
        last=max((i for i,e in enumerate(events) if e[0]=='kernel_complete'),key=lambda i:times[i])
        chain=[];selected=last
        for _ in range(9):
            event=events[selected];operation=work['operations'][event[3]][0] if event[3] is not None else None
            ties=[i for i in event[2] if times[i]==max(times[j] for j in event[2])] if event[2] else []
            chain.append(dict(event=selected,kind=event[0],rank=event[1],time_ns=times[selected],operation=operation,
                              equally_late_declared_dependencies=ties))
            if event[0]=='accepted' and operation and '.c.data.' in operation:final_ids.add(operation)
            if not event[2]:break
            selected=max(event[2],key=lambda i:times[i])
        witnesses[case]=chain
        rows_by_case[case]={op[0]:dict(issued_ns=times[op[10][0]],published_ns=row[4],applied_ns=row[5],source_completed_ns=row[8])
                           for op,row in zip(work['operations'],legacy['operations']) if op[2:4]==[1,'data']}
        del result,work,legacy,net,masks
    assert tamper_checks==4
    for identity in sorted(final_ids):messages[identity]={case:rows[identity] for case,rows in rows_by_case.items()}
    original=load(study/'local.json');corrected=load(study/'local-corrected.json')
    expected=deepcopy(original);del expected['local_failover']['max_events'];assert expected==corrected
    assert sha(study/'run-normal/result.json')==frozen['amendment']['known_normal_result_sha256']
    monitors={path.parent.name:load(path) for path in (root/'monitors').glob('*/probe.json')}
    assert monitors['local']['status']=='child_failed'
    publications=[]
    for name,value in monitors.items():
        command=value['command']
        if not any(Path(arg).name=='publish.py' for arg in command) or '--output' not in command:
            continue
        if Path(command[command.index('--output')+1]).resolve()==delivery:
            publications.append(name)
    assert len(publications)==1
    for name in ('normal','local-corrected','prefix','analyze',publications[0]):
        assert monitors[name]['status']=='completed' and monitors[name]['child_exit_code']==0
    assert all(not value['remaining_live_group_pids'] for value in monitors.values())
    assert sum(value['wall_seconds'] for value in monitors.values())<900
    evidence=dict(passed=True,source_files_independently_verified=15,selected_choices=120,
        unique_dispatch_activation_bytes=1677312,independent_curve_cases=3,curve_tamper_rejections=4,
        method_correction_preserved=True,normal_not_reexecuted=True,new_network_executions=3,
        selected_predecessor_chains=witnesses,same_message_cross_condition_witnesses=messages,
        predecessor_scope='One selected declared-dependency chain with ties retained; not a unique critical path or removable-delay proof',
        monitored_wall_seconds_before_verification=sum(value['wall_seconds'] for value in monitors.values()),
        peak_sampled_group_rss_bytes=max(value['peak_sampled_group_rss_bytes'] for value in monitors.values()),
        source_sha256=sha(Path(__file__)),analysis_sha256=sha(root/'analysis/analysis.json'),
        delivery_sha256=sha(delivery/'delivery.json'),algorithm_innovation_validated=False,goal_complete=False)
    (out/'verification.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({key:evidence[key] for key in ('passed','source_files_independently_verified','independent_curve_cases','curve_tamper_rejections')}))


if __name__=='__main__':main()
