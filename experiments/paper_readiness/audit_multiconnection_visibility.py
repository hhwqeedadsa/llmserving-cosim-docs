"""Read fixed sources and check explicit finite visibility contracts, without executing vendor code."""
import argparse
from collections import Counter
from functools import lru_cache
import hashlib
import json
from pathlib import Path


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')


def finite_cases():
    # Both puts were issued before the modeled prefix. Deliberately strengthen
    # local completion to require remote receipt; visibility still is separate.
    # This is an abstract premise, NOT an assertion about every GIN provider.
    common = dict(receive0=[], receive1=[], local0=['receive0'], local1=['receive1'],
        visible0=['receive0'], visible1=['receive1'], flush=['local0', 'local1'])
    cases = []
    for name, covered in (('local_completion_only', []),
                          ('one_visibility_domain', ['visible0']),
                          ('explicit_all_domain_capability', ['visible0', 'visible1'])):
        deps = {**common, 'signal0':['flush', *covered], 'wait':['signal0'], 'consume':['wait']}
        cases.append(dict(name=name, deps=deps, signals=1,
            contract='abstract target-visibility prerequisites; not a GDAKI hardware model'))
    deps = {**common, 'signal0':['flush', 'visible0'], 'signal1':['flush', 'visible1'],
            'wait':['signal0', 'signal1'], 'consume':['wait']}
    cases.append(dict(name='per_domain_signals', deps=deps, signals=2,
        contract='two abstract visibility domains, explicitly covered separately'))
    return cases


def solve(case):
    names = sorted(case['deps'])
    assert len(names)<=12
    masks = [sum(1<<names.index(d) for d in case['deps'][n]) for n in names]
    full = (1<<len(names))-1
    consumed = 1<<names.index('consume')
    visible = (1<<names.index('visible0')) | (1<<names.index('visible1'))
    visited = set()

    @lru_cache(None)
    def count(state):
        visited.add(state)
        assert len(visited)<=1000000
        if state==full:
            return 1
        return sum(count(state|(1<<i)) for i in range(len(names))
                   if not state&(1<<i) and state&masks[i]==masks[i])

    schedules = count(0)
    assert schedules>0
    @lru_cache(None)
    def bad(state):
        if state&consumed and state&visible!=visible:
            return ()
        for i, name in enumerate(names):
            if not state&(1<<i) and state&masks[i]==masks[i]:
                tail = bad(state|(1<<i))
                if tail is not None:
                    return (name, *tail)
        return None
    witness = bad(0)
    return dict(name=case['name'], events=len(names), reachable_subsets=len(visited),
        complete_topological_orders=schedules, consume_implies_both_visible=witness is None,
        counterexample_prefix=list(witness) if witness is not None else None)


NEEDLES = (
    ('nccl', 'docs/userguide/source/api/device_gin.rst', 'makes no claims regarding the completion'),
    ('nccl', 'src/include/nccl_device/gin.h', 'preceding puts on this context to the same peer'),
    ('nccl', 'src/include/nccl_device/impl/gin_barrier__funcs.h', 'this->fenceAllContexts = true;'),
    ('nccl', 'src/include/nccl_device/impl/gin_barrier__funcs.h', 'signals and puts on different QPs are'),
    ('nccl', 'src/include/nccl_device/impl/gin_barrier__funcs.h', 'int total = nCtx * nPeerSigs;'),
    ('nccl', 'src/include/nccl_device/impl/gin_barrier__funcs.h', 'if ((fence & ncclGinFenceLevel::Put) && !this->net._supportsStrongSignal())'),
    ('nccl', 'src/include/nccl_device/impl/gin_barrier__funcs.h', 'this->team.nRanks : this->team.nRanks - 1'),
    ('nccl', 'src/include/nccl_device/impl/barrier__funcs.h', 'this->gin.thing._flushesAllPutsOnAnySignal()'),
    ('nccl', 'src/include/nccl_device/impl/barrier__funcs.h', '*needsProducerFlush = true;'),
    ('nccl', 'src/include/nccl_device/impl/barrier__funcs.h', "Push this rank's own puts out before the barrier"),
    ('nccl', 'src/include/nccl_device/gin/gdaki/gin_gdaki.h', 'flush every previously-received put when any signal arrives'),
    ('nccl', 'src/include/nccl_device/gin/gdaki/gin_gdaki.h', 'req->sq_rsvd_index = n;'),
    ('nccl', 'src/gin/gin_host.cc', 'localGinDevs[commIdx % nLocalGinDevs]'),
    ('nccl', 'src/include/nccl_device/impl/gin__funcs.h', 'gin->connectionId = comm.ginConnectionCount == 3'),
    ('nccl', 'src/include/nccl_device/impl/gin__funcs.h', 'gin->contextId = comm.ginConnectionCount == 3'),
    ('deepep', 'deep_ep/include/deep_ep/comm/barrier.cuh', 'for (int i = global_warp_idx; i < num_qps;'),
    ('deepep', 'deep_ep/include/deep_ep/comm/barrier.cuh', 'const NCCLGin barrier_gin(nccl_dev_comm, gin.nccl_window, 0,'),
    ('deepep', 'deep_ep/include/deep_ep/comm/handle.cuh', 'gin.waitSignal('),
)


def analyze(source, profile, output):
    assert not output.exists() and output.parent.is_dir()
    output.mkdir()
    rows = json.loads((source/'sources.json').read_text())
    trees = {label: {r['path']:r for r in json.loads((source/(label+'-tree.json')).read_text())['tree']}
             for label in ('nccl','deepep')}
    for r in rows:
        data = (source/r['project']/r['path']).read_bytes()
        assert hashlib.sha256(data).hexdigest()==r['sha256']
        assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==r['blob']
        assert trees[r['project']][r['path']]['sha']==r['blob']
    excerpts = []
    for project, path, needle in NEEDLES:
        lines = (source/project/path).read_text().splitlines()
        matches = [i for i,line in enumerate(lines) if needle in line]
        assert matches, (path,needle)
        # Store ALL occurrences and their context; do not quietly choose a hit.
        for i in matches:
            first,last=max(0,i-2),min(len(lines),i+4)
            excerpts.append(dict(project=project,path=path,needle=needle,line=i+1,
                                 start_line=first+1,text='\n'.join(lines[first:last])))
    cases = finite_cases()
    results = [solve(c) for c in cases]
    assert [r['consume_implies_both_visible'] for r in results]==[False,False,True,True]
    data = profile.read_bytes()
    by_connection,by_context = Counter(),Counter()
    byte_counts = Counter()
    for r in json.loads(data):
        if r['path']=='rdma':
            by_connection[r['connection']]+=r['token_puts']
            byte_counts[r['connection']]+=r['operand_bytes']
            by_context[r['context']]+=r['token_puts']
    summary = dict(source_files=len(rows),excerpt_occurrences=len(excerpts),
        generic_all_context_barrier_issues_per_context=True,
        deepep_custom_barrier_context=0,
        unconditional_generic_flush_remote_visibility=False,
        gdaki_physical_visibility_domain_verified=False,
        library_bug_proven=False,simulated_packets=False,GPU_executed=False,
        independent_requests=False,timing_predictions=False,
        profile_sha256=hashlib.sha256(data).hexdigest(),
        observed_combine_remote_puts_by_declared_connection=dict(sorted(by_connection.items())),
        observed_combine_operand_bytes_by_declared_connection=dict(sorted(byte_counts.items())),
        observed_combine_remote_puts_by_context=dict(sorted(by_context.items())),
        profile_scope='previously seen EP4 48-token prefix, declared 8 contexts/2 connections, not measured NICs',
        illustrative_barrier_API_counts=dict(ranks=4,requested_contexts=17,connections=2,
            allocated_contexts=18,custom_single_context=dict(total=16,remote=12,self=4),
            generic_all_allocated_contexts=dict(total=288,remote=216,self=72),
            scope='one barrier including self; API calls, not WQEs/packets/cost or a like-for-like optimization'),
        unresolved=['GDAKI receiving visibility domain across contexts/connections/NICs',
                    'self-peer physical service and calibrated completion/visibility costs',
                    'active hardware/configuration and independent requests'])
    save(output/'cases.json',cases)
    save(output/'finite-results.json',results)
    save(output/'excerpts.json',excerpts)
    save(output/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--profile',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    analyze(a.source,a.profile,a.output)


if __name__=='__main__':main()
