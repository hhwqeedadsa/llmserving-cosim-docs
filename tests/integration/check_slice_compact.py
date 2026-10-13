"""Lossless finite slice compaction, sampling and replay through the public CLI."""
import argparse
from pathlib import Path
import subprocess
import shutil
import sys

from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.research_storage import stored_rows
from moe_ub_cosim.user_io import read,save,inventory,timestamp


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--slice',type=Path,required=True)
    p.add_argument('--backend',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();root=Path(__file__).resolve().parents[2]
    source=a.slice.resolve();out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    original=inventory(source);example=root/'examples/a3_segment';commands=[]
    def cli(label,*args,fail=False):
        argv=[sys.executable,'-m','moe_ub_cosim',*map(str,args)];started=timestamp()
        r=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=240)
        for stream in ('stdout','stderr'):(out/(label+'.'+stream+'.log')).write_text(getattr(r,stream))
        commands.append(dict(argv=argv,cwd=str(root),started_utc=started,exit_code=r.returncode));save(out/'commands.json',commands)
        assert (r.returncode!=0) if fail else r.returncode==0,(label,r.stderr)
    cli('compact','slice','compact','--slice',source,'--output',out/'compact','--storage','hardlink')
    cli('inspect','slice','inspect','--slice',out/'compact','--verify')
    cli('duplicate','slice','compact','--slice',source,'--output',out/'compact',fail=True)
    assert inventory(source)==original
    manifest=read(out/'compact/slice.json');assert manifest['schema_version']=='moe-ub-research-slice/4'
    assert not (out/'compact/raw/execution').exists()
    for name,digest in read(source/'slice.json')['artifacts'].items():
        if not name.startswith('raw/execution/'):assert manifest['artifacts'][name]==digest
    for mode in ('sample','replay'):
        config=read(example/(mode+'.json'));config['export_policy']=str(example/'export-policy.json')
        config['host']['artifact_roots']=[str(out)]
        if mode=='replay':config['backend']=str(a.backend.resolve())
        save(out/(mode+'.json'),config)
        for label,bundle in [('original',source),('compact',out/'compact')]:
            name=mode+'-'+label
            cli(name,mode,'--slice',bundle,'--config',out/(mode+'.json'),'--output',out/name)
            cli(name+'-validate','validate','--run',out/name)
        for access in ('truth','observed','metrics'):
            directory=out/(mode+'-original')/access
            if not (directory/'storage.json').exists():continue
            for field in read(directory/'storage.json')['fields']:
                assert list(stored_rows(directory,field))==list(stored_rows(out/(mode+'-compact')/access,field)),(mode,access,field)
        if mode=='replay':
            execution=out/'replay-original/raw/execution'
            for field in read(execution/'result.json')['record_storage']['fields']:
                assert list(iter_result(execution,field))==list(iter_result(out/'replay-compact/raw/execution',field)),field
            assert not (out/'replay-compact/work/slice').exists()
            for path in (execution/'ports').glob('*/observation-audit.json'):
                assert read(path)==read(out/'replay-compact/raw/execution'/path.relative_to(execution))
            assert read(out/'replay-compact/manifest.json')['scope']=='dependency_driven_frozen_finite_segment'
    shutil.copytree(out/'compact',out/'corrupt')
    archive=out/'corrupt/archives/execution_archive.tar.gz'
    with archive.open('r+b') as stream:stream.seek(20);value=stream.read(1);stream.seek(20);stream.write(bytes([value[0]^1]))
    cli('corrupt-rejected','slice','inspect','--slice',out/'corrupt','--verify',fail=True)
    save(out/'summary.json',dict(passed=True,all_original_bytes_preserved=True,source_unchanged=True,corruption_rejected=True,
        sample_identical=True,replay_records_and_wire_identical=True,replay_did_not_expand_execution=True,
        original_bytes=sum(v['bytes'] for v in original.values()),compact_bytes=sum(v['bytes'] for v in inventory(out/'compact').values())))
    print('PASS lossless finite slice compaction, identical sample and backend-only replay')


if __name__=='__main__':main()
