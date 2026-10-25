"""Run bundled source in an empty cwd; compare the two entire output reports."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from fixtures.batched_callback_replay.stage import load,save,sha


def main():
    p=argparse.ArgumentParser()
    for name in ('restored','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();root=a.restored.resolve();stage=a.stage.resolve();code=root/'source';cwd=root/'empty-cwd'
    assert Path(__file__).resolve().is_relative_to(code)
    import moe_ub_cosim
    assert Path(moe_ub_cosim.__file__).resolve().is_relative_to(code/'python')
    env=dict(os.environ,PYTHONPATH=str(code/'python')+':'+str(code),PYTHONDONTWRITEBYTECODE='1')
    commands=[
        [sys.executable,'-P','-B','-m','fixtures.batched_callback_replay.audit','--source',root/'evidence/original',
         '--output',root/'fresh-audit.json'],
        [sys.executable,'-P','-B','-m','fixtures.batched_callback_replay.verify','--source',root/'evidence/original',
         '--report',root/'fresh-audit.json','--output',root/'fresh-verification.json']]
    for index,command in enumerate(commands):
        with (root/(f'fresh-{index}.stdout')).open('w') as out,(root/(f'fresh-{index}.stderr')).open('w') as err:
            subprocess.run(command,cwd=cwd,env=env,stdout=out,stderr=err,check=True,timeout=25)
    equal={}
    for fresh,old in (('fresh-audit.json','audit.json'),('fresh-verification.json','verification-final.json')):
        assert (root/fresh).read_bytes()==(root/'evidence'/old).read_bytes()
        equal[fresh]=sha(root/fresh)
    save(stage/'recovery.json',dict(passed=True,network_executed=False,copied_source_root=True,empty_cwd=True,
        commands=[list(map(str,command)) for command in commands],reports_byte_identical=equal,
        scope='offline replay of saved callbacks only; no native build, network or full four-rank completion'))


if __name__=='__main__':main()
