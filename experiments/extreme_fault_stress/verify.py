"""Independent routing, dependency, byte coverage and aggregate-result checks.

Does not import the generator, analyzer, platform, fixtures or tests.
Physical packet/ACK reconstruction is intentionally not claimed.
"""
import argparse
from collections import Counter, defaultdict, deque
import hashlib
import json
from pathlib import Path


def load(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def verify(s, inputs, summary):
    c=load(inputs/'config.json');manifest=load(inputs/'manifest.json')
    assert all(sha(inputs/n)==h for n,h in manifest['files'].items())
    topo=load(s/('topology-'+inputs.name)/'topology.json')
    cases=[]; completed={}
    for case in c['cases']:
        rpath=s/'network'/case['name']/'result.json'
        if not rpath.exists():cases.append(dict(name=case['name'],executed=False));continue
        load_name=case['load'];routes=load(inputs/'truth'/(load_name+'-routes.json'))
        expected=Counter()
        for row in routes:
            assert row['src'] in c['sources'] and len(row['tokens'])==c['tokens_per_source_per_step']
            counts=Counter()
            for token in row['tokens']:
                owner=c['owners'][token['expert']//c['experts_per_owner']]
                assert owner==token['owner'];counts[owner]+=1
                expected[row['src'],owner]+=c['hidden_dim']*c['activation_bytes_per_element']
            assert [counts[o] for o in c['owners']]==c[load_name+'_owner_tokens']
        w=load(inputs/'observed'/(load_name+'-work.json')); labels=load(inputs/'observed'/(load_name+'-labels.json'))
        assert len(w['work'])==90 and len(labels)==90 and len(w['streams'])==32
        r=load(rpath);t=r['persistent_transport'];writes={x['id']:x for x in t['work']};locals_={x['id']:x for x in r['local_work']}
        assert r['passed'] and r['drained'] and t['closed'] and t['physical_drained']
        assert set(writes)|set(locals_)==set(range(90)) and not set(writes)&set(locals_)
        assert not t['recovery']['failed'] and not t['recovery']['omitted_events']
        stream_cfg={x['id']:x for x in w['streams']};events={};unique=0
        phases=Counter()
        for item,label in zip(w['work'],labels):
            wid=item['id'];assert label['id']==wid;phases[label['phase']]+=1
            ready=max([events[x['work'],x['event']] for x in item['after']],default=0)
            if item['kind']=='write':
                actual=writes[wid];src,dst=label['src'],label['dst']; binding=stream_cfg[item['stream']]
                assert (binding['src'],binding['dst'])==(src,dst)
                pair=(src,dst) if label['phase']=='dispatch' else (dst,src)
                assert item['bytes']==expected[pair] and actual['bytes']==item['bytes']
                assert actual['ready_ns']==ready and actual['published_ns']>=ready
                assert actual['accepted_ns']>=actual['published_ns'] and actual['acked_ns']>=actual['accepted_ns']
                events[wid,'accepted']=actual['accepted_ns'];events[wid,'acked']=actual['acked_ns'];unique+=item['bytes']
            else:
                actual=locals_[wid];assert actual['rank']==item['rank'] and actual['ready_ns']==ready
                assert actual['start_ns']>=ready and actual['completed_ns']==actual['start_ns']+item['duration_ns']
                events[wid,'completed']=actual['completed_ns']
        assert unique==4194304 and phases==dict(input=8,dispatch=32,experts=8,combine=32,merge=8,step_done=2)
        streams={x['id']:x for x in t['streams']};seen=defaultdict(list);tx=Counter();packets=Counter()
        for time,stream,src,dst,nic,seq,size,uid in t['source_packets']:
            assert stream in stream_cfg and (src,dst)==(stream_cfg[stream]['src'],stream_cfg[stream]['dst'])
            assert size>0 and seq>=0;seen[stream].append((seq,seq+size));tx[stream]+=size;packets[stream]+=1
        for sid,conf in stream_cfg.items():
            total=sum(x['bytes'] for x in w['work'] if x['kind']=='write' and x['stream']==sid)
            end=0
            for lo,hi in sorted(seen[sid]):assert lo<=end and hi<=total;end=max(end,hi)
            assert end==total==streams[sid]['snd_una']==streams[sid]['submitted_bytes']
            assert tx[sid]==streams[sid]['source_payload_bytes'] and packets[sid]==streams[sid]['source_data_packets']
            assert total<w['transport_recovery']['inflight_bytes']
        pc=r['packet_capture'];assert len(pc['events'])+pc['omitted_events']==pc['observations']
        assert pc['complete']==(pc['omitted_events']==0)
        protocol=load(inputs/'observed'/(case['name']+'-protocol.json'))
        if case['failures']:
            f=r['link_failures'];assert f['configuration']==protocol['link_failures'] and not f['pending_events']
            downs=[x for x in f['events'] if x['kind']=='link_down']
            assert [(x['src'],x['dst'],x['time_ns']) for x in downs]==[tuple(x) for x in case['failures']]
        final=events[89,'completed'];completed[case['name']]=final
        row=next(x for x in summary['cases'] if x['name']==case['name'])
        assert row['application_completion_ns']==final and row['source_payload_bytes']==sum(tx.values())
        assert row['capture_omitted']==pc['omitted_events'] and row['capture_complete']==pc['complete'] and not row['full_packet_path_audited']
        cases.append(dict(name=case['name'],executed=True,passed=True,dependencies=90,write_byte_coverage=True,
                          complete_source_records=True,capture_complete=pc['complete'],packet_records_omitted=pc['omitted_events'],
                          whole_physical_packet_path_audit=False))
    for effect in summary['effects'].values():
        before,after=completed[effect['before']],completed[effect['after']]
        assert effect['delta_ns']==after-before and abs(effect['slowdown_percent']-(after/before-1)*100)<1e-10
    commands=load(s/'commands.json');actual=sum(bool(x.get('native_network_started')) for x in commands)
    assert actual==3==summary['actual_networks'] and summary['phase_closed']
    assert not any((s/'network'/x).exists() for x in ('hotspot-core','hotspot-static-core','hotspot-partition'))
    return dict(passed=True,actual_networks=actual,cases=cases,network_phase_closed=True,
                candidate_optimization_gain_measured=False,all_dependency_and_source_byte_checks=True,
                full_fault_packet_capture=False,full_physical_path_or_ack_reconstruction=False)


def main():
    p=argparse.ArgumentParser()
    for k in ('stage','inputs','analysis','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();d=verify(a.stage.resolve(),a.inputs.resolve(),load(a.analysis));a.output.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps(d,ensure_ascii=False))


if __name__=='__main__':main()
