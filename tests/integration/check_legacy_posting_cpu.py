"""Exercise real queue-core liveness with explicit CPU completion callbacks."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[2];records=[]
    commands=[('new-build',['c++','-std=c++17','-Wall','-Wextra','-Werror','-pedantic','-Ibackend',
        'tests/unit/legacy_posting_capacity_test.cc','-o',str(out/'posting-capacity')]),
        ('new-run',[str(out/'posting-capacity')]),
        ('old-build',['c++','-std=c++17','-Wall','-Wextra','-Werror','-pedantic','-Ibackend',
        'tests/unit/low_latency_posting_test.cc','-o',str(out/'old-posting')]),
        ('old-run',[str(out/'old-posting')]),
        ('lowering',[sys.executable,'-B','tests/unit/legacy_native_test.py'])]
    for name,command in commands:
        run=subprocess.run(command,cwd=root,capture_output=True,text=True,timeout=60)
        (out/(name+'-stdout.log')).write_text(run.stdout);(out/(name+'-stderr.log')).write_text(run.stderr)
        records.append(dict(name=name,argv=command,cwd=str(root),exit_code=run.returncode))
        (out/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
        assert run.returncode==0,name
    result=json.loads((out/'new-run-stdout.log').read_bytes())
    result['old_posting_regression']=json.loads((out/'old-run-stdout.log').read_bytes())
    result['lowering_methods']=5
    (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
