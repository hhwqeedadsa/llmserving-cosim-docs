"""Bounded data-only restoration and offline recompilation of this research."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile

MAX_BYTES=16*2**20


def digest(raw):return hashlib.sha256(raw).hexdigest()


def restore(archive,output):
    if output.exists():raise ValueError('restore requires a new directory')
    if archive.stat().st_size>8*2**20:raise ValueError('archive compressed size cap')
    with tarfile.open(archive,'r:gz') as stream:
        members=[];seen=set();total=0
        for m in stream:
            total+=m.size
            if len(members)>=512 or m.name in seen or total>MAX_BYTES:
                raise ValueError('archive member/count/byte cap')
            p=PurePosixPath(m.name)
            if not m.isfile() or not p.parts or p.is_absolute() or '..' in p.parts or p.as_posix()!=m.name:
                raise ValueError('unsafe archive member')
            members.append(m);seen.add(m.name)
        names=[m.name for m in members]
        if any(str(parent) in seen for name in names for parent in PurePosixPath(name).parents):
            raise ValueError('archive file/directory conflict')
        if 'manifest.json' not in names:raise ValueError('missing manifest')
        manifest=json.loads(stream.extractfile('manifest.json').read())
        if not isinstance(manifest,dict) or set(manifest)!=set(names)-{'manifest.json'}:raise ValueError('manifest member mismatch')
        files={}
        for m in members:
            raw=stream.extractfile(m).read()
            if len(raw)!=m.size:raise ValueError('archive member truncated')
            if m.name!='manifest.json':
                entry=manifest[m.name]
                if not isinstance(entry,dict) or set(entry)!={'bytes','sha256'} or type(entry['bytes']) is not int or \
                   entry['bytes']!=len(raw) or entry['sha256']!=digest(raw):raise ValueError('member hash/size mismatch')
            files[m.name]=raw
    output.mkdir()
    for name,raw in files.items():
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    return manifest


def replay(stage,output):
    output.mkdir();empty=output/'empty';empty.mkdir()
    here=Path(__file__).resolve().parent;records=[]
    def run(name,command,stdout=None):
        r=subprocess.run(list(map(str,command)),capture_output=True,timeout=45,cwd=empty)
        (output/(name+'.stdout')).write_bytes(r.stdout);(output/(name+'.stderr')).write_bytes(r.stderr)
        records.append(dict(name=name,command=list(map(str,command)),cwd=str(empty),exit_code=r.returncode))
        (output/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
        if r.returncode:raise RuntimeError('offline step failed: '+name)
        if stdout:stdout.write_bytes(r.stdout)
    inputs=stage/'inputs';build=output/'build'
    run('build',[sys.executable,'-B',here/'audit.py','build','--inputs',inputs,'--output',build])
    run('execute',[build/'source-harness'],output/'records.jsonl')
    run('evidence',[sys.executable,'-B',here/'audit.py','evidence','--inputs',inputs,'--output',output/'evidence.json'])
    run('analyze',[sys.executable,'-B',here/'audit.py','analyze','--records',output/'records.jsonl','--output',output/'summary.json'])
    run('verify',[sys.executable,'-B',here/'verify.py','--inputs',inputs,'--records',output/'records.jsonl',
                  '--evidence',output/'evidence.json','--summary',output/'summary.json',
                  '--generated',build/'source-harness.cc','--output',output/'verification.json'])
    comparisons={}
    for old,new in [('build-corrected/source-harness.cc','build/source-harness.cc'),
                    ('execute-monitor/stdout.log','records.jsonl'),('evidence.json','evidence.json'),
                    ('summary.json','summary.json'),('verification.json','verification.json')]:
        raw=(output/new).read_bytes()
        if raw!=(stage/old).read_bytes():raise AssertionError('offline result changed: '+new)
        comparisons[new]=dict(bytes=len(raw),sha256=digest(raw))
    (output/'replay.json').write_text(json.dumps(dict(schema_version='communicator-recovery-replay/1',
        five_outputs_byte_identical=True,new_source_root=True,empty_cwd=True,network_requests=0,
        gpu_or_provider_execution=False,comparisons=comparisons),sort_keys=True,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    r=sub.add_parser('restore');r.add_argument('--archive',type=Path,required=True);r.add_argument('--output',type=Path,required=True)
    r=sub.add_parser('replay');r.add_argument('--stage',type=Path,required=True);r.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.action=='restore':print('verified members:',len(restore(a.archive.resolve(),a.output.resolve())))
    else:replay(a.stage.resolve(),a.output.resolve())


if __name__=='__main__':main()
