"""Independent clock/window/ASTRA audits of real multi-port research exports."""
import argparse
from collections import Counter
from copy import deepcopy
import csv
import datetime
from decimal import Decimal
from fractions import Fraction
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys

from check_delay import read,sha
from check_variants import rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2];out=args.output_dir.resolve()
    out.mkdir(parents=True,exist_ok=False);binary=args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service'
    base=read(root/'fixtures/m5_observation/run.json');commands=[];results={};failures={}
    def invoke(label,argv):
        argv=[str(v) for v in argv];start=datetime.datetime.now(datetime.timezone.utc).isoformat()
        result=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=120)
        for stream in ('stdout','stderr'):(out/(label+'.'+stream+'.log')).write_text(getattr(result,stream))
        commands.append(dict(label=label,argv=argv,cwd=str(root),started_utc=start,
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),exit_code=result.returncode))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n');return result
    def research(label,c,expect=None):
        path=out/(label+'.json');path.write_text(json.dumps(c,indent=2)+'\n')
        r=invoke(label,[sys.executable,'-m','moe_ub_cosim.research','run','--config',path,'--backend',binary,'--output-dir',out/label])
        if expect:
            backend_log=out/label/'logs/raw-run/backend.stderr.log'
            diagnostic=r.stderr+(backend_log.read_text() if backend_log.exists() else '')
            assert r.returncode!=0 and expect in diagnostic,(label,diagnostic)
            failures[label]=dict(exit_code=r.returncode,diagnostic=expect);return
        assert r.returncode==0,(label,r.stderr);return audit(label,c)
    def evidence(raw):
        return dict(report=read(raw/'execution/result.json'),front=read(raw/'service_result.json'),protocol=rows(raw/'protocol.jsonl'),
            # Python bytecode timestamps and protoc's output directory are not
            # executable graphs. Compare every semantic/Chakra/schema source,
            # and separately normalize only the recorded compiler output path.
            graphs={str(p.relative_to(raw/'graphs')):sha(p) for p in (raw/'graphs').rglob('*')
                    if p.is_file() and p.suffix!='.pyc' and p.name!='compiler.json'},
            compilers={str(p.relative_to(raw/'graphs')):json.loads(p.read_text().replace(str(raw),'RUN'))
                       for p in (raw/'graphs').rglob('compiler.json')},
            native={str(p.relative_to(raw/'execution/network/runlog')):sha(p) for p in (raw/'execution/network/runlog').rglob('*') if p.is_file()},
            astra=re.sub(r'^\[\d{4}-\d{2}-\d{2} [^]]+\] ','',(raw/'execution/log/log.log').read_text(),flags=re.MULTILINE))
    def local(t,clock):return math.floor(Fraction(10**9+clock['drift_ppb'],10**9)*t+clock['offset_ns'])
    def audit(label,c):
        path=out/label;raw=path/'logs/raw-run';current=evidence(raw);report=current['report']
        manifest=read(path/'manifest.json');check=read(path/'metrics/validation.json')
        assert manifest['normal_exit'] and manifest['calibration_status']=='unvalidated_reference' and check['passed']
        assert manifest['mode']==c['mode'] and manifest['random']==dict(seed=10,run=1)
        assert set(p.name for p in path.iterdir())=={'manifest.json','inputs','graphs','observed','truth','metrics','logs'}
        assert set(p.name for p in (path/'observed').iterdir())=={'endpoint_ops.jsonl','port_windows.csv','public_config.json'}
        assert report['network_initializations']==1 and report['network_drain']['idle']
        assert len(manifest['dependencies'])==13 and manifest['backend_sha256']==sha(binary)
        assert all(sha(path/p)==digest for p,digest in manifest['artifact_hashes'].items())
        observed=rows(path/'observed/endpoint_ops.jsonl');pub=read(path/'observed/public_config.json')
        clocks={(x['device_type'],x['device_id']):x for x in c['observation']['clocks']}
        raw_dirs=sorted((raw/'execution/ports').iterdir())
        original=sorted(rows(raw_dirs[0]/'observed/endpoint_ops.jsonl'),key=lambda r:(r['rank_id'],r['start_local_ns'],r['local_record_id']))
        counters=Counter();mapping={}
        assert len(observed)==len(original)
        for src,dst in zip(original,observed):
            rank=src['rank_id'];counters[rank]+=1;mapping[rank,counters[rank]]=src
            assert dst==dict(rank_id=rank,local_record_id=counters[rank],kind=src['kind'],
                start_local_ns=local(src['start_local_ns'],clocks['rank',rank]),end_local_ns=local(src['end_local_ns'],clocks['rank',rank]))
        with (path/'observed/port_windows.csv').open() as f:port_rows=list(csv.DictReader(f))
        with (path/'truth/attribution_windows.csv').open() as f:attributions=list(csv.DictReader(f))
        expected_attribution=Counter();position=0;zero=0;business_total=0
        for p in pub['ports']:
            switch,port=p['switch_id'],p['port_id'];clock=clocks['switch',switch]
            directory=raw/f'execution/ports/{switch}-{port}'
            events=rows(directory/'packet-events.jsonl')
            trace=raw/f'execution/network/runlog/PortTrace_node_{switch}_port_{port}.tr'
            if trace.exists():
                native=trace.read_text()
            else:
                # ns-3 creates traces lazily. Only this proven empty service
                # may have no native trace; missing nonempty evidence fails.
                assert label=='empty' and not current['front']['requests'] and not events
                assert not report['target_writes'] and current['front']['finished_ns']==0
                assert read(directory/'observation-audit.json')['native_tx_bytes_delta']==0
                native=''
            starts=[(int(Decimal(t)*1000),int(n)) for t,n in re.findall(r'\[([\d.]+)us\] Port Tx, port ID: \d+ PacketSize: (\d+)',native)]
            assert starts==[(e['sim_time_ns'],e['counted_packet_bytes']) for e in events]
            ranges=rows(directory/'truth/packet_ranges.jsonl') if c['observation']['record_provenance'] else []
            for start in range(p['start_local_ns'],p['end_local_ns'],p['period_ns']):
                end=min(start+p['period_ns'],p['end_local_ns']);row=port_rows[position];position+=1
                sizes=[n for t,n in starts if start<=local(t,clock)<end]
                assert row==dict(switch_id=str(switch),port_id=str(port),direction='egress',local_window_start_ns=str(start),
                    local_window_end_ns=str(end),counted_packets=str(len(sizes)),counted_packet_bytes=str(sum(sizes)))
                zero+=not sizes
                parts=[r for r in ranges if start<=local(r['sim_time_ns'],clock)<end]
                for part in parts:
                    key=(part['operation_id'],switch,port,start,end);value=part['task_byte_range'][1]-part['task_byte_range'][0]
                    expected_attribution[key]+=value;business_total+=value
            # Partial last windows are present, not silently rounded to a full period.
            assert int(port_rows[position-1]['local_window_end_ns'])==p['end_local_ns']
        got=Counter({(r['operation_id'],int(r['switch_id']),int(r['port_id']),int(r['local_window_start_ns']),
                      int(r['local_window_end_ns'])):int(r['business_payload_bytes']) for r in attributions})
        assert len(got)==len(attributions) and got==expected_attribution
        assert position==len(port_rows)==check['port_windows'] and zero==check['zero_windows']
        ops=rows(path/'truth/operation_registry.jsonl');transfers=rows(path/'truth/transfer_registry.jsonl')
        if c['observation']['record_provenance']:
            bound=set();plans={p.parent.name:read(p) for p in (path/'graphs').glob('*/plan.json')}
            for op in ops:
                p=plans[op['graph_id']]
                events={(e['rank_id'],e['node_id']):e for e in report['node_execution'] if e['graph_id']==p['graph_id']}
                for ref in op['endpoint_records']:
                    rank=ref['rank_id'];key=(rank,ref['local_record_id']);assert key not in bound;bound.add(key)
                    row=mapping[key];assert (row['start_local_ns'],row['end_local_ns'])==(ref['start_global_ns'],ref['end_global_ns'])
                    g=next(g for g in p['rank_graphs'] if g['rank_id']==rank)
                    if op['kind']=='KV_TRANSFER':
                        begin=p['ready_ns'];end=max([p['ready_ns']]+[e['end_ns'] for (r,n),e in events.items() if r==rank])
                    else:
                        phase=g['phase_nodes']
                        def done(name,fallback):return fallback if phase[name] is None else events[rank,phase[name]]['end_ns']
                        def comm(kind,fallback):return max([fallback]+[events[rank,n['node_id']]['end_ns'] for n in g['nodes'] if n['kind']!='COMP' and n['name'].startswith(kind+'-')])
                        prepared=done('prepare',p['ready_ns']);dispatched=comm('dispatch',prepared)
                        merged=done('local_merge',done('expert',dispatched))
                        begin,end=(prepared,dispatched) if op['kind']=='dispatch' else (merged,done('finish',comm('combine',merged)))
                    assert (begin,end)==(ref['start_global_ns'],ref['end_global_ns'])
            assert bound==set(mapping) and len(transfers)==check['transfers']
            assert sum(t['business_payload_bytes'] for t in transfers)==check['unique_delivery_bytes']
            assert check['attribution_status']=='checked'
        else:
            assert not ops and not transfers and not attributions and check['attribution_status']=='disabled'
        current.update(path=path,observed={p.name:sha(p) for p in (path/'observed').iterdir()},
            check=check,observed_endpoints=observed,port_rows=port_rows,business_port_occurrences=business_total)
        results[label]=current;return current
    on=research('on',base)
    assert on['front']['finished_ns']==264523 and on['check']['transfers']==84 and on['check']['unique_delivery_bytes']==688128
    zero=deepcopy(base)
    for clock in zero['observation']['clocks']:clock.update(offset_ns=0,drift_ppb=0)
    for switch in zero['observation']['switches']:switch.update(start_local_ns=123,end_local_ns=500321)
    zero_run=research('zero',zero)
    assert on['observed']!=zero_run['observed']
    assert any(r['start_local_ns']<0 for r in on['observed_endpoints'])
    off=deepcopy(base);off['observation']['record_provenance']=False;off_run=research('off',off)
    repeated=research('repeat',base)
    assert on['observed']==off_run['observed']==repeated['observed']
    clip=deepcopy(base)
    for switch in clip['observation']['switches']:switch.update(start_local_ns=100000,end_local_ns=120321,period_ns=777)
    research('clip',clip)
    # Position a real packet exactly on a local boundary, with a one-ns tail.
    first=rows(out/'on/logs/raw-run/execution/ports/8-4/packet-events.jsonl')[0]
    clock=next(x for x in base['observation']['clocks'] if x['device_type']=='switch' and x['device_id']==8)
    at=local(first['sim_time_ns'],clock);boundary=deepcopy(base)
    boundary['observation']['switches'][0].update(start_local_ns=at-2,end_local_ns=at+1,period_ns=2)
    edge=research('boundary',boundary)
    r=[r for r in edge['port_rows'] if r['switch_id']=='8' and r['port_id']=='4']
    assert r[0]['counted_packets']=='0' and r[1]['counted_packets']=='1'
    absent=out/'absent';r=invoke('absent',[sys.executable,'-m','moe_ub_cosim.service',
        '--input',on['path']/'inputs/service.json','--layer',on['path']/'inputs/layer.json','--backend',binary,
        '--system',on['path']/'inputs/system.json','--network',on['path']/'inputs/network','--output-dir',absent])
    assert r.returncode==0,r.stderr
    runtime_keys=('report','front','protocol','graphs','compilers','native','astra')
    for label in ('zero','off','repeat','clip','boundary'):
        for key in runtime_keys:assert results[label][key]==on[key],(label,key)
    absent_evidence=evidence(absent)
    for key in runtime_keys:assert absent_evidence[key]==on[key],('absent',key)
    # The P/D case has the same topology plus its explicitly configured 0->4 TP.
    pd=deepcopy(base);pd['inputs'].update(service='fixtures/m4_pd_handoff/service.json',
        layer='fixtures/m4_pd_handoff/layer.json',network='fixtures/m4_pd_handoff/network')
    pd_run=research('pd',pd)
    assert any(r['kind']=='KV_TRANSFER' for r in pd_run['observed_endpoints'])
    empty=read(root/base['inputs']['service']);empty['requests']=[]
    file=out/'empty.service.json';file.write_text(json.dumps(empty)+'\n')
    c=deepcopy(base);c['inputs']['service']=str(file);empty_run=research('empty',c)
    assert empty_run['check']['port_windows']==empty_run['check']['zero_windows'] and not empty_run['observed_endpoints']
    data=deepcopy(base);data['mode']='research';data['inputs'].update(service='fixtures/m4_data_inputs/compute-stage.json',network='fixtures/m2_single_layer/network')
    data['observation']['clocks']=[x for x in data['observation']['clocks'] if x['device_type']=='rank' and x['device_id']<4]+[dict(device_type='switch',device_id=4,offset_ns=-3000,drift_ppb=100000)]
    data['observation']['switches']=[dict(switch_id=4,port_ids=[2],start_local_ns=-2000,end_local_ns=500321,period_ns=5000)]
    data_run=research('data',data)
    assert data_run['front']['finished_ns']==303633 and read(data_run['path']/'manifest.json')['mode']=='research'
    good=invoke('validate-run',[sys.executable,'-m','moe_ub_cosim.research','validate-run','--run-dir',on['path']]);assert good.returncode==0,good.stderr
    isolated=out/'observed-only';shutil.copytree(on['path']/'observed',isolated)
    good=invoke('validate-observed',[sys.executable,'-m','moe_ub_cosim.research','validate-observed','--observed-dir',isolated]);assert good.returncode==0,good.stderr
    for name,relative,error,reseal in (
        ('changed-artifact','truth/clock_models.json','Missing or changed run artifact',False),
        ('truth-mapping','truth/operation_registry.jsonl','Truth differs',True),
        ('missing-provenance','logs/raw-run/execution/ports/8-4/truth/packet_ranges.jsonl','Missing or duplicate business packet provenance',True)):
        d=out/('bad-'+name);shutil.copytree(on['path'],d);file=d/relative
        if name=='changed-artifact':
            value=read(file);value['devices'][0]['offset_ns']+=1;file.write_text(json.dumps(value)+'\n')
        else:
            value=rows(file)
            if name=='truth-mapping':value[0]['endpoint_records'][0]['start_global_ns']+=1
            else:assert value;value.pop(0)
            file.write_text(''.join(json.dumps(row)+'\n' for row in value))
        if reseal:
            # Bypass only the checksum guard to exercise semantic validation.
            manifest=read(d/'manifest.json');manifest['artifact_hashes'][relative]=sha(file)
            (d/'manifest.json').write_text(json.dumps(manifest)+'\n')
        result=invoke('bad-'+name,[sys.executable,'-m','moe_ub_cosim.research','validate-run','--run-dir',d])
        assert result.returncode!=0 and error in result.stderr,(name,result.stderr)
        failures[name]=dict(exit_code=result.returncode,diagnostic=error)
    bad=[]
    def bad_config(name,error,change):
        c=deepcopy(base);change(c);bad.append((name,error,c))
    bad_config('nonmonotone','drift_ppb',lambda c:c['observation']['clocks'][0].update(drift_ppb=-10**9))
    bad_config('overflow','local clock',lambda c:c['observation']['clocks'][0].update(offset_ns=2**63-1))
    bad_config('duplicate-clock','Duplicate device',lambda c:c['observation']['clocks'].append(deepcopy(c['observation']['clocks'][0])))
    bad_config('missing-clock','Missing or unexpected',lambda c:c['observation']['clocks'].pop(0))
    bad_config('duplicate-port','Duplicate observation port',lambda c:c['observation']['switches'][0]['port_ids'].append(0))
    bad_config('period-zero','period',lambda c:c['observation']['switches'][0].update(period_ns=0))
    bad_config('window-before-start','outside simulated',lambda c:c['observation']['switches'][0].update(start_local_ns=-9000))
    bad_config('unknown-port','existing switch port',lambda c:c['observation']['switches'][0].update(port_ids=[999]))
    for name,error,c in bad:research('bad-'+name,c,error)
    failed=out/'bad-unknown-port'
    assert not read(failed/'manifest.json')['normal_exit'] and not read(failed/'metrics/validation.json')['passed']
    for name in ('missing-window','hidden-endpoint','hidden-public','extra-file'):
        d=out/('bad-'+name);shutil.copytree(on['path']/'observed',d)
        if name=='missing-window':
            p=d/'port_windows.csv';lines=p.read_text().splitlines();del lines[-1];p.write_text('\n'.join(lines)+'\n')
        elif name=='hidden-endpoint':
            p=d/'endpoint_ops.jsonl';records=rows(p);records[0]['operation_id']='hidden';p.write_text(''.join(json.dumps(r)+'\n' for r in records))
        elif name=='hidden-public':
            p=d/'public_config.json';record=read(p);record['clock_models']=base['observation']['clocks'];p.write_text(json.dumps(record)+'\n')
        else:(d/'truth.json').write_text('{}\n')
        r=invoke('bad-'+name,[sys.executable,'-m','moe_ub_cosim.research','validate-observed','--observed-dir',d])
        assert r.returncode!=0,(name,r.stdout);failures[name]=dict(exit_code=r.returncode)
    source=[p for folder in ('backend','python/moe_ub_cosim') for p in (root/folder).iterdir() if p.is_file() and p.suffix in ('.cc','.hh','.py')]
    summary=dict(passed=True,actual_research_runs=len(results),unobserved_runs=1,failures=failures,
        metrics={name:dict(finished_ns=r['front']['finished_ns'],transfers=r['check']['transfers'],
            unique_bytes=r['check']['unique_delivery_bytes'],endpoint_records=r['check']['endpoint_records'],
            port_windows=r['check']['port_windows'],zero_windows=r['check']['zero_windows']) for name,r in results.items()},
        source_hashes={str(p.relative_to(root)):sha(p) for p in source+[Path(__file__).resolve(),root/'fixtures/m5_observation/run.json']})
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps({k:v for k,v in summary.items() if k!='source_hashes'},indent=2))


if __name__=='__main__':main()
