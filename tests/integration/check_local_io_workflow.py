"""Actual receive -> guarded local copy -> actual packet forwarding; no GPU costs."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from check_remote_transaction_workflow import audit
from check_source_window_workflow import prefix_audit
from check_transaction_local_stage_workflow import endpoint_audit, local_audit

ROOT = Path(__file__).resolve().parents[2]


def read(path): return json.loads(path.read_text())
def save(path, value): path.write_text(json.dumps(value, indent=2)+'\n')
def find(config, identity): return next(o for o in config['operations'] if o['id'] == identity)


def io_audit(result, config):
    summary = prefix_audit(result, config, 64)
    rows = {r['id']: r for r in result['local_work']}
    specs = {o['id']: o for o in config['operations'] if 'copy' in o}
    for identity in specs:
        row = rows[identity]
        events = [e for e in result['transactions']['local_memory_events'] if e['operation'] == identity]
        expected = (['acquired'] if row['ready_ns'] >= 0 else [])+(['captured'] if row['start_ns'] >= 0 else [])+(
            ['committed', 'released'] if row['completed_ns'] >= 0 else [])
        assert [e['kind'] for e in events] == expected
    summary.update(copy_nodes=len(specs),completed_copies=sum(rows[i]['completed_ns'] >= 0 for i in specs),
                   active_local_leases=len(result['transactions']['active_local_memory_leases']))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--reference-backend', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    a = parser.parse_args(); backend, reference, out = (p.resolve() for p in (a.backend, a.reference_backend, a.output))
    assert backend.is_file() and reference.is_file() and not out.exists()
    assert shutil.disk_usage(out.parent).free >= 8*2**30+64*2**20
    out.mkdir(); started = time.monotonic(); commands = []
    env = dict(os.environ, PYTHONPATH=str(ROOT/'python'), PYTHONDONTWRITEBYTECODE='1')

    def invoke(name, argv, expected=0):
        before = time.monotonic(); remaining = 55-(before-started); assert remaining > 0
        with (out/(name+'.stdout')).open('w') as stdout, (out/(name+'.stderr')).open('w') as stderr:
            p = subprocess.run(list(map(str, argv)), cwd=out, env=env, stdout=stdout, stderr=stderr, timeout=remaining)
        commands.append(dict(argv=list(map(str, argv)), cwd=str(out), exitcode=p.returncode,
                             expected_exitcode=expected, wall_seconds=time.monotonic()-before))
        save(out/'commands.json', commands); assert p.returncode == expected, name

    def cli(name, argv, expected=0): invoke(name, [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', *argv], expected)

    def run(name, config, expected=0, binary=backend):
        save(out/(name+'-input.json'), config)
        cli(name, ['rdma-transaction-probe', '--topology', 'topology', '--protocol', 'protocol.json',
                   '--config', name+'-input.json', '--backend', binary, '--output', name], expected)
        if (out/name/'result.json').exists(): return read(out/name/'result.json')

    topology = read(ROOT/'examples/scaleout_rdma/small-clos-100g.json')
    protocol = read(ROOT/'examples/scaleout_rdma/rdma.json'); protocol.update(payload_bytes=64, chunk_bytes=256)
    protocol['packet_capture'] = dict(nodes=[0,1,4],start_ns=0,end_ns=5000000,max_events=10000)
    save(out/'topology-input.json', topology); save(out/'protocol.json', protocol)
    cli('generate', ['topology', '--config', 'topology-input.json', '--output', 'topology'])
    config = read(ROOT/'examples/scaleout_rdma_transactions/local-io.json')
    normal = run('normal', config); summary = io_audit(normal, config); full = audit(normal, config, 64)
    local_audit(normal, config); endpoint_count = endpoint_audit(normal, config)
    assert normal['completed_operations'] == 7 and normal['completed_local_work'] == 4
    assert not normal['transactions']['active_local_memory_leases'] and not normal['transactions']['active_source_leases']
    local = {r['id']:r for r in normal['local_work']}; assert local[1]['ready_ns'] < local[1]['start_ns'] < local[1]['completed_ns']
    original = bytes.fromhex(config['windows'][0]['hex'])[:257]
    for e in normal['transactions']['local_memory_events']:
        if e['kind'] in ('captured', 'committed'): assert bytes.fromhex(e['hex']) == original
    for owner,key,address in [(1,9,12672),(0,7,4608)]:
        w, = [w for w in normal['transactions']['windows'] if (w['owner'],w['key']) == (owner,key)]
        assert bytes.fromhex(w['hex'])[address-w['address']:address-w['address']+257] == original
    run('repeat', config); assert (out/'normal/result.json').read_bytes() == (out/'repeat/result.json').read_bytes()
    changed = deepcopy(config); data = bytearray.fromhex(changed['windows'][0]['hex']); data[8:257] = bytes(x^0x55 for x in data[8:257]); changed['windows'][0]['hex'] = data.hex()
    assert changed['operations'] == config['operations']
    mutation = run('changed-input', changed); io_audit(mutation, changed); audit(mutation, changed, 64)
    for e in mutation['transactions']['local_memory_events']:
        if e['kind'] in ('captured', 'committed'): assert bytes.fromhex(e['hex']) == bytes(data[:257])
    assert all(bytes.fromhex(e['hex']) == bytes(data[:257]) for e in mutation['transactions']['source_events'] if e['kind']=='captured')
    renamed = deepcopy(config)
    for op in renamed['operations']:
        op['id'] += 100
        for d in op['after']: d['operation'] += 100
    rename = run('renamed', renamed); io_audit(rename, renamed); audit(rename, renamed, 64)
    for key in ('source_packets','source_payload_packets'):
        assert normal['transactions']['transport'][key] == rename['transactions']['transport'][key]
    zero = deepcopy(config)
    for op in zero['operations']:
        if 'copy' in op: op['duration_ns'] = 0
    z = run('zero-duration', zero); io_audit(z, zero); local_audit(z, zero); audit(z, zero, 64)
    shared = deepcopy(config)
    shared['operations'].append(dict(id=30,kind='local',rank=4,duration_ns=0,after=[dict(operation=10,event='completed')],
                                    copy=dict(source=dict(key=8,address=8192,bytes=257),target=dict(key=8,address=9216))))
    s = run('shared-input', shared); shared_summary = io_audit(s, shared); local_audit(s, shared); audit(s, shared, 64)
    l = {r['id']:r for r in s['local_work']};assert l[30]['ready_ns'] < l[1]['completed_ns'] == l[30]['start_ns']

    failures = {}
    for name in ['input-put','running-input-put','output-put','output-atomic','early-source-read','local-reset','conflicting-admission','output-condition-read']:
        case = deepcopy(config)
        if name in ('input-put','running-input-put','output-put','output-atomic'):
            op = find(case,6);op['after'] = [dict(operation=10,event='completed')]
            if name == 'running-input-put': op['after'].append(dict(operation=20,event='completed'))
            if name not in ('input-put','running-input-put'): op['address'] = 8576
            if name == 'output-atomic': op.update(verb='fetch_add',value=1);del op['hex']
        elif name == 'early-source-read':
            find(case,2)['after'] = [dict(operation=10,event='completed')]
        else:
            op = dict(id=30,kind='local',rank=4,duration_ns=0,after=[dict(operation=10,event='completed')])
            if name == 'output-condition-read':
                op['local_wait'] = [dict(key=8,address=8576,compare='eq',value=0)]
            else:
                op['local_wait'] = [dict(key=8,address=8960,compare='eq',value=0,reset_to=1)]
                if name == 'local-reset':
                    op['local_wait'].append(dict(key=8,address=8192,compare='eq',value=int.from_bytes(original[:8],'little'),reset_to=0))
                else: op['copy'] = deepcopy(find(case,1)['copy'])
            case['operations'].append(op)
        result = run(name, case, expected=1);failures[name] = io_audit(result, case)
        assert not result['passed'] and 'overlaps active' in result['execution_error']
        assert {r['operation'] for r in result['transactions']['active_local_memory_leases']} == {1}
        row, = [r for r in result['local_work'] if r['id'] == 1]
        assert row['completed_ns'] == -1
        assert (row['start_ns'] >= 0) == (name == 'running-input-put') # protect queued and running
        w, = [w for w in result['transactions']['windows'] if w['owner']==4]
        assert bytes.fromhex(w['hex'])[:257] == original and bytes.fromhex(w['hex'])[384:384+257] == b'\0'*257
        assert bytes.fromhex(w['hex'])[768:776] == b'\0'*8
        if name in ('local-reset','conflicting-admission','output-condition-read'):
            row, = [r for r in result['pending_local_work'] if r['id'] == 30];assert not row['admitted']
            assert all(e['operation'] != 30 for e in result['transactions']['local_events'])
            if name == 'output-condition-read': assert 'read overlaps active local output lease' == row['local_wait_error']

    cutoff = {}
    for name,identity in [('running-cutoff',1),('queued-cutoff',20)]:
        case=deepcopy(config);find(case,identity)['duration_ns']=5000000
        result=run(name,case,expected=1);cutoff[name]=io_audit(result,case)
        assert result['finish_ns']==5000000 and 'execution_error' not in result and not result['passed']
        assert {r['operation'] for r in result['transactions']['active_local_memory_leases']} == {1}
        events=[e['kind'] for e in result['transactions']['local_memory_events'] if e['operation']==1]
        assert events == (['acquired','captured'] if identity==1 else ['acquired'])
    missing=deepcopy(config);find(missing,10)['value']=0
    blocked=run('missing-count',missing,expected=1);missing_summary=io_audit(blocked,missing)
    assert not blocked['transactions']['local_memory_events'] and blocked['completed_operations']==2

    legacy=read(ROOT/'examples/scaleout_rdma_transactions/source-windows.json')
    run('v3-old',legacy,binary=reference);run('v3-new',legacy)
    assert (out/'v3-old/result.json').read_bytes()==(out/'v3-new/result.json').read_bytes()
    plain=deepcopy(legacy);plain['schema_version']='moe-rdma-transactions/4';plain['local_memory_model']=deepcopy(config['local_memory_model'])
    converted=run('no-copy-v4',plain)
    for key in ('local_memory_model','local_memory_events','active_local_memory_leases'): converted['transactions'].pop(key)
    assert converted==read(out/'v3-old/result.json')

    invalid=[]
    for name in ['wrong-model','bool-size','foreign-owner','overlap','target-overflow','extra-target-size','own-reset','old-schema']:
        case=deepcopy(config);op=find(case,1)
        if name=='wrong-model':case['local_memory_model']['capture']='admission/1'
        elif name=='bool-size':op['copy']['source']['bytes']=True
        elif name=='foreign-owner':op['copy']['source']['owner']=4
        elif name=='overlap':op['copy']['target']['address']=8192
        elif name=='target-overflow':op['copy']['target']['address']=9600
        elif name=='extra-target-size':op['copy']['target']['bytes']=257
        elif name=='own-reset':op['local_wait']=[dict(key=8,address=8576,compare='eq',value=0,reset_to=0)]
        else:case['schema_version']='moe-rdma-transactions/3';del case['local_memory_model']
        assert run(name,case,expected=1) is None and not (out/name).exists()
        invoke(name+'-native',[backend,out/'topology/topology.json',out/'protocol.json',out/(name+'-input.json'),out/(name+'-native.json')],1)
        assert not (out/(name+'-native.json')).exists();invalid.append(name)
    total=sum(p.stat().st_size for p in out.rglob('*') if p.is_file());assert total<64*2**20
    summary=dict(passed=True,normal=summary,complete_audit=full,endpoint_frames_checked=endpoint_count,
        only_initial_bytes_changed=True,all_copy_and_wire_bytes_changed=True,queued_input_and_output_protected=True,
        shared_input=shared_summary,conflicts=failures,cutoff=cutoff,missing_count=missing_summary,
        repeat_byte_identical=True,id_renaming_wire_invariant=True,zero_duration=True,v3_byte_identical=True,no_copy_v4_equal=True,
        invalid_cli_and_native=invalid,commands=len(commands),public_commands=sum(c['argv'][1]=='-m' for c in commands),
        direct_native_commands=sum(c['argv'][1]!='-m' for c in commands),wall_seconds=time.monotonic()-started,output_bytes=total,
        backend_sha256=hashlib.sha256(backend.read_bytes()).hexdigest(),reference_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
        scope='declared-duration identity byte copy with input/output guards; not expert arithmetic, CUDA DMA, GIN visibility or calibrated costs')
    save(out/'verification.json',summary);print(json.dumps(summary))


if __name__=='__main__':main()
