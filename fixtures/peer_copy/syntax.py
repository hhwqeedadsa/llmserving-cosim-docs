"""Check changed native units against a fixed existing compile database only."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repository',type=Path,required=True)
    p.add_argument('--database',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();a.output.mkdir(parents=True)
    rows=json.loads(a.database.read_text());records=[]
    for name in ('fabric.cc','persistent_streams.cc','remote_transactions.cc','transaction_probe.cc','transaction_session.cc'):
        source=(a.repository/'backend/rdma'/name).resolve()
        row=next(r for r in rows if Path(r['file']).resolve()==source)
        args=row.get('arguments') or shlex.split(row['command'])
        command=[];i=0
        while i<len(args):
            arg=args[i]
            if arg in ('-o','-MF','-MT','-MQ'):i+=2;continue
            if arg in ('-c','-MD','-MMD'):i+=1;continue
            command.append(arg);i+=1
        command.append('-fsyntax-only')
        run=subprocess.run(command,cwd=row['directory'],capture_output=True,timeout=50)
        (a.output/(name+'.stdout')).write_bytes(run.stdout);(a.output/(name+'.stderr')).write_bytes(run.stderr)
        records.append(dict(file=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                            command=command,cwd=row['directory'],exitcode=run.returncode))
        (a.output/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
        assert run.returncode==0,name
    print(json.dumps(dict(passed=True,translation_units=5,object_files_written=False,networks=0)))


if __name__=='__main__':main()
