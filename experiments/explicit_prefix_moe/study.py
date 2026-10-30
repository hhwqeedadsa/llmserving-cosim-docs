"""Freeze/execute/analyze an independently prepared real-route repair study."""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
import gc
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
NEW=('normal','local-w60','prefix-w20','prefix-w60')
OLD=('normal','global-w20','local-w20','instant-w20','global-w60','local-w60','instant-w60')
def load(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def hashes(d):return {str(f.relative_to(d)):sha(f) for f in sorted(d.rglob('*')) if f.is_file()}
def save(p,v):
    with Path(p).open('x') as s:json.dump(v,s,ensure_ascii=False,indent=2);s.write('\n')


def public(command,receipt):
    save(receipt,dict(command=list(map(str,command)),cwd=str(ROOT),status='started'))
    with receipt.with_suffix('.stdout.log').open('x') as out,receipt.with_suffix('.stderr.log').open('x') as err:
        r=subprocess.run(command,cwd=ROOT,stdout=out,stderr=err)
    v=load(receipt);v.update(exit_code=r.returncode,status='completed' if r.returncode==0 else 'failed')
    receipt.write_text(json.dumps(v,indent=2)+'\n')
    if r.returncode:raise SystemExit('Failed public command retained at '+str(receipt))


def normalized(c):
    c=deepcopy(c);c['route_replay']['manifest']='<verified content>';return c


def route_identity(c,base):
    ref=c['route_replay']['manifest'];path=base/ref['path'];assert sha(path)==ref['sha256']
    v=load(path)
    for row in v['files']:
        assert sha(base/row['file']['path'])==row['file']['sha256']
        row['file']['path']='<verified content>'
    return v


def prepare(a):
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    build=a.build.resolve();prov=load(build/'provenance.json');backend=load(build/'backend.json')
    for name,h in prov['sources'].items():assert sha(ROOT/name)==h,name
    for path in backend['linked_ns3_libraries']:assert sha(path)==backend['library_hashes'][Path(path).name]
    reference=a.reference.resolve();normal=reference/'normal'
    assert load(normal/'verification.json')['passed']
    p=out/'input'
    public([sys.executable,'-B',str(ROOT/'examples/scaleout_message_buffers/prepare.py'),
      '--source-manifest',str(a.source_manifest.resolve()),'--source-base',str(a.source_base.resolve()),
      '--route-files','15','--output',str(p)],out/'prepare-command.json')
    c=load(p/'service-buffers.json');c['inflight_helpers']['policy']='observe_only/1'
    c['transport']['transport_recovery']['initial_rto_ns']=17000
    assert normalized(c)==normalized(load(normal/'input.json'))
    assert route_identity(c,ROOT)==route_identity(load(normal/'input.json'),normal)
    assert load(p/'topology/topology.json')==load(normal/'topology.json')
    protocol=load(p/'rdma-buffers.json');del protocol['data_loss'];assert protocol==load(normal/'rdma.json')
    save(p/'service-fixed.json',c);save(p/'normal.json',protocol)
    for name in NEW[1:]:
        cfg=deepcopy(protocol);window=20000 if name.endswith('w20') else 60000
        cfg['link_failures']=dict(mode='switch_links/2',events=[dict(src=128,dst=132,time_ns=4000,
          route_update_delay_ns=60000,restore_time_ns=4000+window,restore_route_update_delay_ns=60000)])
        cfg['local_failover']=dict(mode='live_ecmp/1') if name.startswith('local') else dict(mode='explicit_prefix/1',max_events=100000)
        if name=='local-w60':assert cfg==load(reference/name/'rdma.json')
        save(p/(name+'.json'),cfg)
    routes={row['file']['path']:row['file']['sha256'] for row in load(p/'buffer-source-manifest.json')['files']}
    assert len(routes)==len(set(routes.values()))==15
    sources=[*sorted((ROOT/'python/moe_ub_cosim').glob('*.py')),*sorted((ROOT/'examples').glob('*/prepare.py')),
      *sorted(Path(__file__).parent.glob('*.py')),Path(__file__).with_name('README.md')]
    save(out/'frozen.json',dict(schema_version='moe-explicit-prefix-study/1',network_executed=False,
      platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
      backend=backend['service'],backend_sha256=sha(backend['service']),compiled_sources_sha256=prov['sources'],
      libraries_sha256={str(p):sha(p) for p in backend['linked_ns3_libraries']},
      source_sha256={str(p.relative_to(ROOT)):sha(p) for p in sources},prepared=str(p),prepared_sha256=hashes(p),
      routes_sha256=routes,reference=str(reference),reference_sha256={name:{f:sha(reference/name/f) for f in
        ('input.json','rdma.json','topology.json','frontend.json','result.json','verification.json')} for name in OLD},
      graph_sha256=hashes(normal/'graphs'),new_cases=NEW,old_cases=OLD,max_new_networks=4,
      budget=dict(monitored_seconds=1800,artifact_bytes=2*2**30,min_free_bytes=8*2**30)))
    print(json.dumps(dict(prepared=True,network_executed=False,requests=15,max_new_networks=4)))


def checked(study):
    f=load(study/'frozen.json');assert sha(f['backend'])==f['backend_sha256']
    for group in ('compiled_sources_sha256','source_sha256'):
        for name,h in f[group].items():assert sha(ROOT/name)==h,name
    for group in ('routes_sha256','libraries_sha256'):
        for name,h in f[group].items():assert sha(name)==h,name
    for name,files in f['reference_sha256'].items():
        for path,h in files.items():assert sha(Path(f['reference'])/name/path)==h
    assert hashes(Path(f['prepared']))==f['prepared_sha256'];return f


def run(a):
    study=a.study.resolve();f=checked(study);p=Path(f['prepared'])
    i=NEW.index(a.case)
    for prior in NEW[:i]:assert load(study/(prior+'-command.json'))['exit_code']==0
    if i>0:assert load(study/'normal-compatible.json')['passed']
    if i>1:assert load(study/'local-w60-compatible.json')['passed']
    assert sum((study/n).exists() for n in NEW)<4 and not (study/a.case).exists()
    public([sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-run','--config',str(p/'service-fixed.json'),
      '--protocol',str(p/(a.case+'.json')),'--topology',str(p/'topology'),'--backend',f['backend'],
      '--protoc',str(a.protoc.resolve()),'--output',str(study/a.case)],study/(a.case+'-command.json'))
    print(json.dumps(dict(case=a.case,completed=True,result_sha256=sha(study/a.case/'result.json'))))


def compatible(a):
    f=checked(a.study);assert a.case in ('normal','local-w60')
    old,new=load(Path(f['reference'])/a.case/'result.json'),load(a.study/a.case/'result.json')
    for r in (old,new):del r['loaded_libraries']
    assert old==new,'full raw behavior differs beyond library paths'
    assert normalized(load(Path(f['reference'])/a.case/'input.json'))==normalized(load(a.study/a.case/'input.json'))
    save(a.study/(a.case+'-compatible.json'),dict(passed=True,case=a.case,full_raw_result_identical_except_library_paths=True,
      old_sha256=sha(Path(f['reference'])/a.case/'result.json'),new_sha256=sha(a.study/a.case/'result.json')))
    print(json.dumps(dict(passed=True,case=a.case)))


def summarize(d):
    r=load(d/'result.json');assert r['passed'] and r['drained'] and load(d/'verification.json')['passed']
    n=r['service_transport']['native'];messages=r['service_transport']['messages'];high=defaultdict(int);total=0
    for raw in n['source_packets']:
        x=dict(zip(n['source_packet_columns'],raw));end=x['sequence']+x['payload_bytes'];assert x['sequence']<=high[x['stream']]
        high[x['stream']]=max(high[x['stream']],end);total+=x['payload_bytes']
    logical=sum(m['bytes'] for m in messages);assert sum(high.values())==logical
    history=load(d/'frontend.json')['history'];groups={str(i):max(h['finished_ns'] for h in history if h['group_index']==i) for i in range(4)}
    cap=r['packet_capture'];cols=cap['columns'];codes=cap['event_codes'];drops=Counter();drop_groups=Counter();wrapped=0;repair_counts=Counter()
    stream_groups={s['id']:s['src']%4 for s in n['streams']}
    for x in cap['events']:
        if x[cols.index('event')] in {codes.get('drop'),codes.get('local_drop')}:
            drops[str(x[cols.index('ip_protocol')])]+=1
            if x[cols.index('message_id')] in stream_groups:drop_groups[str(stream_groups[x[cols.index('message_id')]])]+=1
    if 'explicit_repair' in r:
        repair=r['explicit_repair'];repair_counts=Counter(str(row[2]) for row in repair['events'])
        wrapped=sum(meta[0] for row,meta in zip(cap['events'],repair['packet_metadata']) if row[cols.index('event')]==codes['tx'])
    return dict(directory=str(d),result_sha256=sha(d/'result.json'),group_complete_ns=groups,max_group_complete_ns=max(groups.values()),
      drain_ns=r['finish_ns'],messages=len(messages),logical_payload_bytes=logical,source_payload_bytes=total,
      retransmitted_payload_bytes=total-logical,model_wire_bytes=sum(p['model_tx_bytes'] for p in r['ports']),
      encapsulation_wire_bytes=wrapped,repair_counts=dict(repair_counts),drops_by_protocol=dict(drops),drops_by_source_group=dict(drop_groups),
      queue_peak_bytes=max((row[3] for row in r['queue_events']),default=0),
      queue_peak_scope='native switch BEgressQueue hook samples, not NIC ACK queues or residence integral',
      buffer_peak_bytes=r['service_transport']['message_buffers']['peak_bytes'],
      graphs_sha256=hashes(d/'graphs'))


def analyze(a):
    f=checked(a.study);reference=Path(f['reference']);rows={}
    for name in OLD:
        d=a.study/name if name in ('normal','local-w60') else reference/name
        rows[name]=summarize(d);gc.collect()
    for name in NEW[2:]:rows[name]=summarize(a.study/name);gc.collect()
    effects={};normal=rows['normal']['max_group_complete_ns']
    for window in ('20','60'):
        local,prefix,instant=(rows[n+'-w'+window] for n in ('local','prefix','instant'))
        saved=local['max_group_complete_ns']-prefix['max_group_complete_ns'];penalty=local['max_group_complete_ns']-normal
        effects[window]=dict(saved_ns=saved,relative_local_percent=100*saved/local['max_group_complete_ns'],
          fault_penalty_recovered_percent=100*saved/penalty if penalty>0 else None,
          group_saved_ns={str(i):local['group_complete_ns'][str(i)]-prefix['group_complete_ns'][str(i)] for i in range(4)},
          prefix_minus_normal_ns=prefix['max_group_complete_ns']-normal,prefix_minus_instant_ns=prefix['max_group_complete_ns']-instant['max_group_complete_ns'],
          source_payload_delta_bytes=prefix['source_payload_bytes']-local['source_payload_bytes'],
          wire_delta_bytes=prefix['model_wire_bytes']-local['model_wire_bytes'],drain_saved_ns=local['drain_ns']-prefix['drain_ns'])
    save(a.output,dict(passed=True,frozen_sha256=sha(a.study/'frozen.json'),new_networks=4,reused_comparison_conditions=5,
      cases=rows,effects=effects,scope='mature explicit-repair baseline, no novel protocol or full671B/hardware claim'))
    print(json.dumps(dict(passed=True,effects=effects)))


def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
    q=s.add_parser('prepare')
    for name in ('source-manifest','source-base','build','reference','output'):q.add_argument('--'+name,type=Path,required=True)
    q=s.add_parser('run');q.add_argument('--study',type=Path,required=True);q.add_argument('--case',choices=NEW,required=True);q.add_argument('--protoc',type=Path,required=True)
    q=s.add_parser('compatible');q.add_argument('--study',type=Path,required=True);q.add_argument('--case',choices=('normal','local-w60'),required=True)
    q=s.add_parser('analyze');q.add_argument('--study',type=Path,required=True);q.add_argument('--output',type=Path,required=True)
    a=p.parse_args();globals()[a.command](a)


if __name__=='__main__':main()
