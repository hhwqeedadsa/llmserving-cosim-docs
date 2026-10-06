"""Direct aggregates, classified application truth, selected details and sampling limits."""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys
import yaml

from check_operation_queue import read,save,queue_oracle
from moe_ub_cosim.observation import local_time
from moe_ub_cosim.record_storage import iter_result,iter_port_records
from moe_ub_cosim.research_storage import stored_rows


def coarsen(rows,period,*,queues=False):
    grouped={}
    for row in rows:
        left=row['local_window_start_ns']//period*period
        key=row['switch_id'],row['port_id'],left
        b=dict(switch_id=key[0],port_id=key[1],direction='egress',local_window_start_ns=left,
               local_window_end_ns=min(left+period,1000000))
        values=grouped.setdefault(key,dict(b))
        for k,v in row.items():
            if k in b or k.endswith('_mean_bytes'):continue
            values[k]=max(values.get(k,0),v) if k.endswith('_peak_bytes') else values.get(k,0)+v
    for row in grouped.values():
        if queues:
            for kind in ('voq_waiting','fifo_waiting'):
                row[kind+'_mean_bytes']=row[kind+'_byte_ns']/(row['local_window_end_ns']-row['local_window_start_ns'])
    return [grouped[k] for k in sorted(grouped)]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build-dir',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();root=Path(__file__).resolve().parents[2];out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    example=root/'examples/first_round';fixture=root/'fixtures/m13_endpoint_service';binary=a.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    commands=[]
    def invoke(label,argv,fail=False):
        argv=list(map(str,argv));start=datetime.now(timezone.utc).isoformat();r=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=360)
        for stream in ('stdout','stderr'):(out/(label+'.'+stream+'.log')).write_text(getattr(r,stream))
        commands.append(dict(label=label,argv=argv,cwd=str(root),started_utc=start,exit_code=r.returncode));save(out/'commands.json',commands)
        assert (r.returncode!=0) if fail else r.returncode==0,(label,r.stderr)
        return r
    def cli(label,*words,fail=False):return invoke(label,[sys.executable,'-m','moe_ub_cosim',*words],fail)
    scenario=yaml.safe_load((fixture/'scenario.yaml').read_text())
    for k in ('service','layer','system'):scenario[k]=str((fixture/scenario[k]).resolve())
    scenario['backend']['reference_file']=str((fixture/scenario['backend']['reference_file']).resolve())
    scenario['network']['fabric_file']=str((fixture/scenario['network']['fabric_file']).resolve())
    (out/'scenario.yaml').write_text(yaml.safe_dump(scenario,sort_keys=False))
    invoke('resolve',[sys.executable,'-m','moe_ub_cosim.scenario','resolve','--input',out/'scenario.yaml','--backend',binary,'--output-dir',out/'resolved'])
    config=read(example/'run.json');config['backend']=str(binary)
    config['inputs']={k:str(out/'resolved'/(k+'.json' if k!='network' else k)) for k in ('service','layer','system','network')}
    for k in ('record_policy','export_policy'):config[k]=str(example/config[k])
    observation=read(root/'fixtures/m15_aggregate_capture/observation.json');reports={};configs={};cost={}
    for label in ('legacy','detailed','aggregate','selected','count-only'):
        chosen=deepcopy(observation)
        if label=='legacy':chosen.pop('recording');chosen['schema_version']='moe-ub-observation-config/2'
        if label=='detailed':chosen['recording']['mode']='detailed'
        if label=='selected':chosen['recording']['detail_ranges']=[dict(switch_id=4,port_id=4,start_ns=2000,end_ns=36000)]
        if label=='count-only':chosen['recording']['truth_categories']=['dispatch_count']
        configs[label]=chosen;config['observation']=str(save(out/(label+'-observation.json'),chosen))
        cli(label,'run','--config',save(out/(label+'-run.json'),config),'--output',out/label)
        cli('validate-'+label,'validate','--run',out/label)
        reports[label]=read(out/label/'raw/execution/result.json');r=read(out/label/'logs/resources-run/probe.json')
        cost[label]={k:r[k] for k in ('wall_seconds','peak_sampled_group_rss_bytes','max_waited_child_rss_kib')}
        cost[label]['record_bytes']=sum(f.stat().st_size for f in (out/label/'raw/execution/records').iterdir() if f.is_file())
    base=reports['legacy']
    for label in ('detailed','aggregate','selected','count-only'):
        assert reports[label]['simulation_end_ns']==base['simulation_end_ns']
        for field in ('transfers','native_starts','native_completions','node_execution','local_deliveries','endpoint_ops','operation_stages'):
            assert list(iter_result(out/label/'raw/execution',field))==list(iter_result(out/'legacy/raw/execution',field)),(label,field)
        for name in ('endpoint_ops.jsonl','endpoint_stages.jsonl','port_windows.csv','queue_windows.csv'):
            assert list(stored_rows(out/label/'observed',name))==list(stored_rows(out/'legacy/observed',name)),(label,name)
        for port in range(5):
            path=f'raw/execution/ports/4-{port}/observation-audit.json'
            assert read(out/label/path)==read(out/'legacy'/path)
        life=reports[label]['resource_lifecycle']['provenance']
        assert life['native_payloads_retired_at_arrival']>life['peak_resident_packets']>0
        assert life['resident_packets']==life['resident_transfers']==life['pending_native_requests']==0
    truth=list(stored_rows(out/'detailed/truth','message_attribution_windows.csv'))
    categories=Counter()
    for row in truth:categories[row['message_category']]+=row['application_bytes']
    assert set(categories)==set(observation['recording']['truth_categories'])
    for label in ('aggregate','selected'):
        assert list(stored_rows(out/label/'truth','message_attribution_windows.csv'))==truth
        assert list(stored_rows(out/label/'truth','attribution_windows.csv'))==list(stored_rows(out/'legacy/truth','attribution_windows.csv'))
    assert not list(stored_rows(out/'count-only/truth','attribution_windows.csv'))
    assert list(stored_rows(out/'count-only/truth','message_attribution_windows.csv'))==[r for r in truth if r['message_category']=='dispatch_count']
    for port in range(5):
        aggregate=read(out/f'aggregate/raw/execution/ports/4-{port}/record-storage.json')
        assert not any('events' in k or 'packet_ranges' in k for k in aggregate['fields'])
        resource=read(out/f'aggregate/raw/execution/ports/4-{port}/resource-audit.json')
        assert resource['event_bytes']==resource['detail_events']==0
    for label in ('aggregate','detailed','selected','legacy'):
        cli('freeze-'+label,'slice','create','--from-run',out/label,'--output',out/(label+'-slice'))
        info=json.loads(cli('inspect-'+label,'slice','inspect','--slice',out/(label+'-slice')).stdout)
        assert info['capture_capabilities']['storage']==('detailed' if label in ('legacy','detailed') else 'aggregate_with_selected_details')
    sample=read(example/'sample.json');sample.update(period_ns=1270,export_policy=str(example/'export-policy.json'))
    cli('coarsen','sample','--slice',out/'aggregate-slice','--config',save(out/'coarsen.json',sample),'--output',out/'coarsen')
    for name,queues in [('port_windows.csv',False),('queue_windows.csv',True)]:
        assert list(stored_rows(out/'coarsen/observed',name))==coarsen(stored_rows(out/'aggregate/observed',name),1270,queues=queues)
    # Force the detailed path by a non-aligned period; compare to legacy full
    # packet/state history, which provides an independent source representation.
    arbitrary=deepcopy(configs['detailed']);arbitrary['switches'][0]['period_ns']=131
    sample.pop('period_ns');sample['observation']=str(save(out/'arbitrary-observation.json',arbitrary))
    cli('detail-sample','sample','--slice',out/'detailed-slice','--config',save(out/'detail-sample.json',sample),'--output',out/'detail-sample')
    arbitrary.pop('recording');arbitrary['schema_version']='moe-ub-observation-config/2';sample['observation']=str(save(out/'old-arbitrary.json',arbitrary))
    cli('legacy-sample','sample','--slice',out/'legacy-slice','--config',save(out/'old-sample.json',sample),'--output',out/'legacy-sample')
    for scope,names in [('observed',['port_windows.csv','queue_windows.csv']),('truth',['attribution_windows.csv'])]:
        for name in names:assert list(stored_rows(out/'detail-sample'/scope,name))==list(stored_rows(out/'legacy-sample'/scope,name))
    selected=deepcopy(configs['selected']);selected['switches'][0].update(port_ids=[4],start_local_ns=3000,end_local_ns=35003,period_ns=113)
    selected['clocks'][-1].update(offset_ns=-3,drift_ppb=100000000);sample['observation']=str(save(out/'selected-sample-observation.json',selected))
    cli('selected-sample','sample','--slice',out/'selected-slice','--config',save(out/'selected-sample.json',sample),'--output',out/'selected-sample')
    source=out/'legacy/raw/execution';events=list(iter_port_records(source,source/'ports/4-4','queue-state-events.jsonl'))
    expected=queue_oracle(events,selected['switches'][0],selected['clocks'][-1])
    actual={r['local_window_start_ns']:{k:v for k,v in r.items() if k.startswith(('voq_','fifo_'))} for r in stored_rows(out/'selected-sample/observed','queue_windows.csv')}
    assert actual==expected
    rejected=0
    for label,mutate in [('finer',lambda c:c['switches'][0].update(period_ns=113)),
                         ('shifted',lambda c:c['switches'][0].update(start_local_ns=1)),
                         ('clock',lambda c:c['clocks'][-1].update(drift_ppb=1)),
                         ('unrecorded-port',lambda c:c['switches'][0].update(port_ids=[7]))]:
        bad=deepcopy(configs['aggregate']);mutate(bad);sample['observation']=str(save(out/(label+'.json'),bad))
        target=out/('reject-'+label)
        cli('reject-'+label,'sample','--slice',out/'aggregate-slice','--config',save(out/(label+'-sample.json'),sample),'--output',target,fail=True)
        assert not target.exists();rejected+=1
    bad=deepcopy(selected);bad['switches'][0]['start_local_ns']=1000;sample['observation']=str(save(out/'outside-detail.json',bad))
    cli('outside-detail','sample','--slice',out/'selected-slice','--config',save(out/'outside-detail-sample.json',sample),'--output',out/'rejected-outside',fail=True)
    assert not (out/'rejected-outside').exists();rejected+=1
    replay=read(example/'replay.json');replay.update(backend=str(binary),export_policy=str(example/'export-policy.json'))
    cli('replay','replay','--slice',out/'aggregate-slice','--config',save(out/'replay.json',replay),'--output',out/'replay')
    for scope in ('observed','truth','metrics'):
        for name in read(out/'aggregate'/scope/'storage.json')['fields']:
            assert list(stored_rows(out/'aggregate'/scope,name))==list(stored_rows(out/'replay'/scope,name)),(scope,name)
    for label,mutate,error in [('detail-limit',lambda c:c['recording'].update(mode='detailed',max_detail_events_per_port=2),'event count budget'),
                               ('attribution-limit',lambda c:(c['recording'].update(max_window_attribution_entries=1),c['switches'][0].update(period_ns=1000000)),'attribution entry budget')]:
        bad=deepcopy(observation);mutate(bad);config['observation']=str(save(out/(label+'-observation.json'),bad))
        cli(label,'run','--config',save(out/(label+'-run.json'),config),'--output',out/label,fail=True)
        assert error in (out/label/'raw/backend.stderr.log').read_text()
    save(out/'summary.json',dict(passed=True,simulation_end_ns=base['simulation_end_ns'],physical_packets=base['network_drain']['started_packets'],
        categories=dict(categories),direct_aggregate_equals_detail=True,selected_detail_state_oracle=True,
        packet_area_peak_coarsening=True,unsupported_sampling_refused_before_output=rejected,finite_detail_and_attribution_budgets=True,
        old_detailed_reading=True,replay_identical=True,protocol_and_wire_time_unchanged=True,
        provenance_retirement=reports['aggregate']['resource_lifecycle']['provenance'],resources=cost))
    print('PASS direct aggregates, classified zero-business messages, exact bounded detail sampling, old detailed compatibility and replay')


if __name__=='__main__':main()
