"""Four original requests -> public offline plan CLI -> independent operation audit.

No tests, fixtures, private graph generator, simulator backend or GPU is used.
Resource-limited subprocess logs and qualitative failures are retained.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def prepare(path, out):
    assert path.is_file() and path.stat().st_size <= 2**20
    manifest = json.loads(path.read_text())
    assert manifest['source']['kind'] == 'published_model_trace'
    assert manifest['source']['dataset_revision'] == '27febb7b2d24169560a9f6f83d38eb53f94916cf'
    assert manifest['model']['hidden_dim'] == 7168
    assert [f['file_id'] for f in manifest['files']] == ['local-1472','local-1808','local-2675','local-6447']
    records, identities, total = [], [], 0
    for item in manifest['files']:
        source = (ROOT/item['file']['path']).resolve()
        assert source.is_file() and source.stat().st_size <= 16*2**20
        total += source.stat().st_size
        assert total <= 16*2**20 and sha(source) == item['file']['sha256']
        raw = json.loads(source.read_text())
        assert len(raw) > 2
        records.append(raw)
        identities.append(dict(file_id=item['file_id'], sha256=sha(source), bytes=source.stat().st_size,
                               original_path=item['original_path']))
    calls, selected = [], []
    for entry in (1, 2):
        for lid in (3, 4):
            layer = manifest['layers'][lid]
            assert layer['kind'] == 'moe' and layer['num_experts'] == 256 and layer['top_k'] == 8
            tokens = []
            for rank, raw in enumerate(records):
                row = raw[entry][str(lid)]
                assert len(row) == 1 and len(row[0]) == len(set(row[0])) == 8
                assert all(type(e) is int and 0 <= e < 256 for e in row[0])
                tokens.append(row)
                selected.append(dict(call=len(calls), rank=rank, source_entry=entry, source_layer_id=lid,
                                     file_id=identities[rank]['file_id'], expert_ids=row[0]))
            calls.append(dict(name=f'entry{entry}.layer{lid}', tokens=tokens))
    config = dict(schema_version='moe-deepep-ll-plan-input/1',
                  model=dict(experts=256, top_k=8, hidden_size=7168, dispatch_format='bf16'),
                  execution=dict(ranks=4, api='compute_stream_wait_no_hook', cross_rank_path='rdma_only',
                                 nics_per_rank=1, max_tokens_per_rank=1), calls=calls,
                  limits=dict(max_events=65536, max_operations=16384, max_input_bytes=2**20, max_output_bytes=16*2**20))
    save(out/'input.json', config)
    e0 = next(e for e in range(64, 256) if e not in calls[0]['tokens'][0][0])
    data = next(e for e in calls[0]['tokens'][0][0] if e//64 != 0)
    provenance = dict(manifest=dict(path=str(path.relative_to(ROOT)), sha256=sha(path)),
        source=manifest['source'], files=identities, selections=selected, input_bytes=total,
        deployment='four logical ranks; one original request/rank; contiguous equal experts; cross-rank P2P off',
        interpretation='four MoE invocations in original entry/layer order; no measured between-layer time',
        calibrated=False, full_r1_deployment_claim=False, eos_inferred=False,
        probes=dict(zero_count=f'm0.d.count.s0.e{e0}.visible', flag=f'm0.c.flag.d0.e{e0}.visible',
                    sender_done=f'm0.d.data.s0.t0.e{data}.sender_done'))
    save(out/'provenance.json', provenance)
    return config, provenance


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (2*2**30, 2*2**30))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_FSIZE, (17*2**20, 17*2**20))


def rss_kib(pid):
    """Sample time(1) and its actual children; the process group is owned here."""
    total = 0
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                total += int(line.split()[1])
        children = Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
        total += sum(rss_kib(int(child)) for child in children)
    except (FileNotFoundError, ProcessLookupError):
        pass
    return total


def analyze(out, config, provenance):
    plan = json.loads((out/'plan/plan.json').read_text())
    expected = []
    for ci, call in enumerate(config['calls']):
        counts = Counter()
        for rank, rows in enumerate(call['tokens']):
            for token, choices in enumerate(rows):
                for e in choices:
                    owner = e//64
                    expected.extend([(ci,'dispatch','data',rank,owner,e,token,14352),
                                     (ci,'combine','data',owner,rank,e,token,14336)])
                    counts[rank, e] += 1
            for e in range(256):
                expected.extend([(ci,'dispatch','count',rank,e//64,e,None,4),
                                 (ci,'combine','flag',e//64,rank,e,None,4)])
        for op in (o for o in plan['operations'] if o['call'] == ci):
            if op['kind'] == 'count':
                assert op['value'] == -counts[op['src'], op['expert']]-1
                assert op['contributions'] == counts[op['src'], op['expert']]
            elif op['kind'] == 'flag':
                assert op['value'] == 1 and op['contributions'] == counts[op['dst'], op['expert']]
    actual = [(o['call'],o['phase'],o['kind'],o['src'],o['dst'],o['expert'],o['token'],o['operand_bytes'])
              for o in plan['operations']]
    assert Counter(actual) == Counter(expected)
    for name in ('input.json','manifest.json','plan.json'):
        assert sha(out/'plan'/name) == sha(out/'repeat'/name)
    checks = {name:json.loads((out/(name+'.json')).read_text()) for name in ('normal','zero_count','flag','sender_done')}
    assert checks['normal']['all_kernel_completions_reachable']
    assert len(checks['normal']['reachable_kernel_completions']) == 32
    assert len(checks['zero_count']['blocked_kernel_completions']) == 29
    assert len(checks['flag']['blocked_kernel_completions']) == 25
    assert checks['sender_done']['all_kernel_completions_reachable']
    assert all(not c['simulated_packets'] and not c['latency_predicted'] for c in checks.values())
    remote = [o for o in plan['operations'] if o['path'] == 'rdma']
    count = Counter(o['kind'] for o in remote)
    zero = Counter(o['kind'] for o in remote if o['kind'] != 'data' and o['contributions'] == 0)
    summary = dict(scope='original four-request choices in an untimed application partial order; not packet simulation',
        platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        plan_sha256=sha(out/'plan/plan.json'), input_sha256=sha(out/'input.json'),
        events=len(plan['events']), operations=len(actual), remote_operations=dict(count),
        zero_contribution_remote_notifications=dict(zero),
        remote_bf16_data_operand_bytes=sum(o['operand_bytes'] for o in remote if o['kind']=='data'),
        source_identities_verified=4, individual_choice_rows_verified=len(provenance['selections']),
        independently_enumerated_operations=len(expected), repeated_artifact_files_byte_identical=3,
        probes={name:dict(withheld=c['withheld'],blocked=len(c['blocked_kernel_completions']),
                         reachable=len(c['reachable_kernel_completions'])) for name,c in checks.items()},
        source_buffer_reuse_validated=False, hardware_calibrated=False, latency_predicted=False,
        packet_execution=False, overall_paper_ready=False)
    assert dict(count) == dict(data=182, count=3072, flag=3072)
    assert dict(zero) == dict(count=2981, flag=2981)
    save(out/'summary.json', summary)
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    out = a.output.resolve()
    assert not out.exists() and out.parent.is_dir()
    assert hasattr(os, 'wait4') and shutil.disk_usage(out.parent).free >= 8*2**30+64*2**20
    out.mkdir()
    config, provenance = prepare(a.manifest.resolve(), out)
    env = dict(os.environ, PYTHONPATH=str(ROOT/'python'), PYTHONDONTWRITEBYTECODE='1')
    records, started = [], time.monotonic()

    def cli(name, argv):
        args = [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', *map(str,argv)]
        begin, peak, reason = time.monotonic(), 0, None
        with (out/(name+'.stdout')).open('w') as stdout, (out/(name+'.stderr')).open('w') as stderr:
            process = subprocess.Popen(args,cwd=ROOT,env=env,stdout=stdout,stderr=stderr,
                                       preexec_fn=limits,start_new_session=True)
            while True:
                waited, status, usage = os.wait4(process.pid, os.WNOHANG)
                if waited:
                    process.returncode = os.waitstatus_to_exitcode(status)
                    break
                peak = max(peak, rss_kib(process.pid))
                size = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
                if time.monotonic()-begin > 60 or time.monotonic()-started > 300:
                    reason = 'wall budget exceeded'
                elif peak > 2**20:
                    reason = 'sampled process-tree RSS exceeded 1GiB'
                elif size > 64*2**20:
                    reason = 'study output exceeded 64MiB'
                if reason:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    _, status, usage = os.wait4(process.pid, 0)
                    process.returncode = os.waitstatus_to_exitcode(status)
                    break
                time.sleep(.05)
            code = process.returncode
        stats = dict(peak_rss_kib=usage.ru_maxrss, user_seconds=usage.ru_utime, system_seconds=usage.ru_stime,
                     measurement='Linux wait4 child rusage; sampled process-tree RSS separately')
        save(out/(name+'.resource.json'), stats)
        if stats['peak_rss_kib'] > 2**20:
            reason = 'measured child peak RSS exceeded 1GiB'
        records.append(dict(argv=args, cwd=str(ROOT), exit_code=code,reason=reason,
                            wall_seconds=time.monotonic()-begin,sampled_tree_peak_rss_kib=peak,resource=stats))
        save(out/'commands.json',records)
        assert code == 0 and reason is None, 'Preserve failed run; no automatic retry'

    cli('compile', ['deepep-plan','--config',out/'input.json','--output',out/'plan'])
    for name in ('normal','zero_count','flag','sender_done'):
        args = ['deepep-check','--plan',out/'plan','--output',out/(name+'.json')]
        if name != 'normal':
            args += ['--withhold',provenance['probes'][name]]
        cli(name,args)
    cli('repeat', ['deepep-plan','--config',out/'input.json','--output',out/'repeat'])
    print(json.dumps(analyze(out, config, provenance),ensure_ascii=False))


if __name__ == '__main__':
    main()
