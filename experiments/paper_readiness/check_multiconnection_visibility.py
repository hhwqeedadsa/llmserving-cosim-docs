"""Independent finite-order enumeration and source/receipt verifier; no producer imports."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def load(path):return json.loads(path.read_text())
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def check(source, profile, analysis, repeat, output):
    nc='12df1a11afad322be5a204a2db890161cbf8131d'
    ep='f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
    identity=load(source/'nccl-identity.json')
    commit=load(source/'nccl-commit.json')
    assert identity['commit']==commit['sha']==nc
    assert commit['tree']['sha']==load(source/'nccl-tree.json')['sha']==identity['tree']
    for old,new in (('tree.json','nccl-tree.json'),('commit.json','nccl-commit.json')):
        receipt,=[r for r in identity['requests'] if r['file']==old]
        assert digest(source/new)==receipt['sha256']
    ep_receipt=load(source/'deepep-tree-source.json')
    assert ep_receipt['commit']==ep
    receipt,=[r for r in ep_receipt['requests'] if '/git/trees/' in r['url']]
    assert digest(source/'deepep-tree.json')==receipt['sha256']
    manifests=load(source/'sources.json')
    assert len(manifests)==11
    for r in manifests:
        p=source/r['project']/r['path']; b=p.read_bytes()
        assert len(b)==r['size'] and digest(p)==r['sha256']
        assert hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()==r['blob']
        tree=load(source/(r['project']+'-tree.json'))
        row,=[x for x in tree['tree'] if x['path']==r['path']]
        assert row['sha']==r['blob'] and row['size']==len(b)
        assert r['commit']==(nc if r['project']=='nccl' else ep)
    requests=load(source/'requests.json')
    assert len(requests)==3 and [r['exit_code'] for r in requests]==[1,0,0]
    assert sum(r['bytes'] for r in requests)<=768*1024
    assert not (source/'nccl/src/include/nccl_device/gin_barrier.h').exists()
    # Source text is inspected as data only, never compiled, imported or run.
    text=(source/'nccl/src/include/nccl_device/impl/gin_barrier__funcs.h').read_text()
    assert 'this->fenceAllContexts = true;' in text
    branch=text[text.index('if (this->fenceAllContexts) {',text.index('int nPeerSigs')):]
    assert branch.index('signalPeer(scratch, peer)')<branch.index('waitForPeer(scratch, peer)')
    assert branch.count('ncclGin scratch(this->net.comm, ctx, this->net.resourceSharingMode);')==2
    assert 'int total = nCtx * nPeerSigs;' in branch
    assert 'signals and puts on different QPs are' in branch
    for r in load(analysis/'excerpts.json'):
        lines=(source/r['project']/r['path']).read_text().splitlines()
        n=r['text'].count('\n')+1
        assert '\n'.join(lines[r['start_line']-1:r['start_line']-1+n])==r['text']
        assert r['needle'] in lines[r['line']-1]
    base={'receive0':[], 'receive1':[], 'local0':['receive0'], 'local1':['receive1'],
          'visible0':['receive0'], 'visible1':['receive1'], 'flush':['local0','local1']}
    expected={
      'local_completion_only':dict(base, signal0=['flush'],wait=['signal0'],consume=['wait']),
      'one_visibility_domain':dict(base,signal0=['flush','visible0'],wait=['signal0'],consume=['wait']),
      'explicit_all_domain_capability':dict(base,signal0=['flush','visible0','visible1'],wait=['signal0'],consume=['wait']),
      'per_domain_signals':dict(base,signal0=['flush','visible0'],signal1=['flush','visible1'],wait=['signal0','signal1'],consume=['wait'])}
    results={r['name']:r for r in load(analysis/'finite-results.json')}
    totals=[]
    for case in load(analysis/'cases.json'):
        deps=case['deps'];assert deps==expected[case['name']]
        visited=set();orders=bad_orders=prefixes=0
        def walk(order,seen,violated):
            nonlocal orders,bad_orders,prefixes
            prefixes+=1;assert prefixes<=1000000
            visited.add(frozenset(seen))
            violated=violated or ('consume' in seen and not {'visible0','visible1'}<=seen)
            if len(seen)==len(deps):
                orders+=1;bad_orders+=int(violated);return
            for name in sorted(deps):
                if name not in seen and set(deps[name])<=seen:
                    walk(order+[name],seen|{name},violated)
        walk([],set(),False)
        r=results[case['name']]
        assert orders==r['complete_topological_orders'] and len(visited)==r['reachable_subsets']
        assert (bad_orders==0)==r['consume_implies_both_visible']
        witness=r['counterexample_prefix']
        if witness is not None:
            seen=set()
            for event in witness:
                assert event not in seen and set(deps[event])<=seen;seen.add(event)
            assert 'consume' in seen and not {'visible0','visible1'}<=seen
        totals.append(dict(name=case['name'],complete_orders=orders,
            orders_with_early_consume=bad_orders,enumerated_prefixes=prefixes))
    counts=Counter();byte_counts=Counter();ctx=Counter()
    for r in load(profile):
        if r['path']=='rdma':
            counts[str(r['connection'])]+=r['token_puts'];byte_counts[str(r['connection'])]+=r['operand_bytes']
            ctx[str(r['context'])]+=r['token_puts']
    summary=load(analysis/'summary.json')
    assert summary['profile_sha256']==digest(profile)
    assert dict(counts)==summary['observed_combine_remote_puts_by_declared_connection']
    assert dict(byte_counts)==summary['observed_combine_operand_bytes_by_declared_connection']
    assert dict(ctx)==summary['observed_combine_remote_puts_by_context']
    assert sum(counts.values())==117 and sum(byte_counts.values())==1684800
    assert summary['library_bug_proven'] is summary['gdaki_physical_visibility_domain_verified'] is False
    repeated=[]
    for p in sorted(analysis.iterdir()):
        assert p.read_bytes()==(repeat/p.name).read_bytes();repeated.append(p.name)
    value=dict(verified=True,source_files=len(manifests),commit_tree_blob_sha256_checked=True,
        excerpt_occurrences=len(load(analysis/'excerpts.json')),finite_cases=totals,
        total_complete_topological_orders=sum(r['complete_orders'] for r in totals),
        repeated_analysis_files=repeated,failed_source_request_not_retried=True,
        missing_header_not_required_for_implementation_body_claims=True,
        source_CODE_executed=False,network_or_GPU_simulated=False,hardware_counterexample_claimed=False)
    assert not output.exists()
    output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(value,ensure_ascii=False))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('source','profile','analysis','repeat','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();check(a.source,a.profile,a.analysis,a.repeat,a.output)


if __name__=='__main__':main()
