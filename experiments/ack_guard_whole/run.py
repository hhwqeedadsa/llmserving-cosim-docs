"""Run one frozen case through the public CLI; never overwrite or retry."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from prepare import ROOT,files,load,save,sha

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--frozen',type=Path,required=True)
    p.add_argument('--case',choices=('fixed-normal','fixed-local','guarded-normal','guarded-local'),required=True)
    p.add_argument('--protoc',type=Path,required=True);a=p.parse_args();f=load(a.frozen)
    assert f['passed'] and files(Path(f['prepared']))==f['prepared_sha256']
    assert sha(Path(f['backend']['path']))==f['backend']['sha256']
    assert sha(Path(__file__).with_name('protocol.md'))==f['method_sha256']
    for path,digest in f['source_sha256'].items():assert sha(ROOT/path)==digest
    for path,digest in f['loaded_ns3_libraries'].items():assert sha(Path(path))==digest
    case=f['new_cases'][a.case];out=Path(case['output']);assert not out.exists()
    command=[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-run',
        '--config',case['config'],'--protocol',case['protocol'],'--topology',case['topology'],
        '--backend',f['backend']['path'],'--protoc',str(a.protoc.resolve()),'--output',str(out)]
    receipt=a.frozen.parent/(a.case+'-command.json')
    save(receipt,dict(command=command,cwd=str(ROOT),status='started',runner_sha256=sha(Path(__file__))))
    completed=subprocess.run(command,cwd=ROOT)
    record=load(receipt);record.update(status='completed' if completed.returncode==0 else 'failed',exit_code=completed.returncode)
    receipt.write_text(json.dumps(record,indent=2)+'\n')
    raise SystemExit(completed.returncode)

if __name__=='__main__':main()
