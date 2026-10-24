"""Frozen research-only archive; bounded admission and offline source replay."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from experiments.reordering_baseline.checkpoint import admit as admit_format, SCHEMA
from experiments.nic_wakeup_effect.stage import load,save,sha


def admit(path):
    payload,manifest=admit_format(path)
    assert manifest['experiment']=='nic_wakeup_effect' and not manifest['native_runtime_bundled']
    return payload,manifest


def create(a):
    repo=a.repository.resolve();s=a.stage.resolve();assert not a.output.exists()
    assert load(s/'run.json')['phase_closed'] and load(s/'verification.json')['passed'] and load(s/'eligibility-verification.json')['passed']
    files={}
    for dirname in ('inputs','planned','topology','network','source-history','archive-safety'):
        for p in sorted((s/dirname).rglob('*')):
            if p.is_file():files['evidence/'+str(p.relative_to(s))]=p
    for p in sorted(s.iterdir()):
        if p.is_file() and p.suffix in ('.py','.json'):files['evidence/'+p.name]=p
    # A running monitor's samples/logs are mutable; only sealed terminal records.
    for p in sorted(s.glob('*-monitor/*')):
        if p.is_file() and (p.parent/'probe.json').exists():files['evidence/monitors/'+str(p.relative_to(s))]=p
    for p in sorted((repo/'experiments/nic_wakeup_effect').iterdir()):
        if p.is_file():files['source/experiments/nic_wakeup_effect/'+p.name]=p
    reused={
        'local_fault_baselines':('graph_summary.py','analyze.py','mechanism.py','checks.py','routing.py','verify_mechanism.py'),
        'reordering_baseline':('stage.py','checkpoint.py','train.py','verify_train.py','verify.py')}
    for directory,names in reused.items():
        for name in names:
            p=repo/'experiments'/directory/name;files['source/'+str(p.relative_to(repo))]=p
    report=repo/'docs/reviews/源NIC修复后的完整应用与拥塞反馈_20261024.md'
    if report.exists():files['source/'+str(report.relative_to(repo))]=report
    entries={}
    for name,p in files.items():
        assert not p.is_symlink() and p.stat().st_size<=16*2**20
        entries[name]=dict(bytes=p.stat().st_size,sha256=sha(p))
    assert len(entries)<2048 and sum(e['bytes'] for e in entries.values())<128*2**20
    manifest=dict(schema_version=SCHEMA,experiment='nic_wakeup_effect',files=entries,native_runtime_bundled=False,
        scope='six immutable application cases; three new fixed networks; standard bounded research archive format reused')
    with tarfile.open(a.output,'x:gz',compresslevel=6) as ar:
        for name,p in files.items():
            b=p.read_bytes();assert hashlib.sha256(b).hexdigest()==entries[name]['sha256']
            m=tarfile.TarInfo(name);m.size=len(b);ar.addfile(m,io.BytesIO(b))
        b=(json.dumps(manifest,indent=2)+'\n').encode();m=tarfile.TarInfo('manifest.json');m.size=len(b);ar.addfile(m,io.BytesIO(b))
    admit(a.output)
    save(a.output.with_suffix('.receipt.json'),dict(sha256=sha(a.output),members=len(entries)+1,
        uncompressed_bytes=sum(e['bytes'] for e in entries.values())+len(b),compressed_bytes=a.output.stat().st_size,native_runtime_bundled=False))


def restore(a):
    assert not a.output.exists();payload,manifest=admit(a.archive);a.output.mkdir()
    for name,b in payload.items():
        p=a.output/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
    root=a.output.resolve();s=root/'evidence';src=root/'source';cwd=root/'empty-cwd';cwd.mkdir()
    env=dict(os.environ,PYTHONPATH=str(src),PYTHONDONTWRITEBYTECODE='1');commands=[]
    jobs=[
        ('analysis','analyze',['--observed',s/'inputs/observed','--planned',s/'planned','--network',s/'network','--baseline',s/'inputs/baseline']),
        ('verification','verify',['--stage',s,'--analysis',root/'analysis.json']),
        ('eligibility','eligibility',['--stage',s,'--analysis',root/'analysis.json']),
        ('eligibility-verification','verify_eligibility',['--stage',s,'--analysis',root/'analysis.json','--eligibility',root/'eligibility.json']),
        ('summary-final','summary',['--analysis',root/'analysis.json','--eligibility',root/'eligibility.json']),
        ('diagnostics','diagnostics',['--stage',s,'--analysis',root/'analysis.json'])]
    for label,module,args in jobs:
        cmd=[sys.executable,'-B','-m','experiments.nic_wakeup_effect.'+module,*map(str,args),'--output',str(root/(label+'.json'))]
        with (root/(label+'.stdout.log')).open('w') as stdout,(root/(label+'.stderr.log')).open('w') as stderr:
            r=subprocess.run(cmd,cwd=cwd,env=env,stdout=stdout,stderr=stderr,timeout=60)
        commands.append(dict(command=cmd,cwd=str(cwd),pythonpath=str(src),exit_code=r.returncode));save(root/'commands.json',commands)
        assert r.returncode==0,label+': offline replay failed; do not rerun networks'
        assert (root/(label+'.json')).read_bytes()==(s/(label+'.json')).read_bytes(),label+': output differs'
    save(root/'replay.json',dict(passed=True,archive_sha256=sha(a.archive),members_verified=len(payload),
        six_reports_byte_identical=True,new_research_source_root=True,empty_cwd=True,network_executions=0,
        imports='restored research source only; no old repository, platform, fixtures, tests, native programs/libraries',commands=commands))


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    c=sub.add_parser('create')
    for k in ('repository','stage','output'):c.add_argument('--'+k,type=Path,required=True)
    r=sub.add_parser('restore')
    for k in ('archive','output'):r.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();dict(create=create,restore=restore)[a.action](a)


if __name__=='__main__':main()
