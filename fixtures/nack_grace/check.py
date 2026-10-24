"""Bounded CPU and public Python input checks, before any native network."""
import argparse
from pathlib import Path
import sys
from stage import run,save


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage'):p.add_argument('--'+k,type=Path,required=True)
    p.add_argument('--remaining-after-include-fix',action='store_true')
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve()
    for name in (('persistent_recovery',) if a.remaining_after_include_fix else ('nack_grace','selective_repeat','persistent_recovery')):
        run(repo,s,'cpu-build-'+name+('-include-fixed' if a.remaining_after_include_fix else ''),['c++','-std=c++17','-O2','-Wall','-Wextra','-Werror',
            '-I'+str(repo/'backend/rdma'),'-I'+str(repo/'backend'),repo/'tests/unit'/(name+'_test.cc'),'-o',s/(name+'-test')])
        run(repo,s,'cpu-test-'+name,[s/(name+'-test')])
    tests=['scaleout_nack_grace_test.py','scaleout_selective_repeat_test.py','persistent_recovery_input_test.py',
        'scaleout_transaction_selective_test.py','scaleout_causal_waves_test.py','scaleout_persistent_scale_test.py']
    for name in tests:
        run(repo,s,'python-'+name,[sys.executable,'-B','-m','unittest','discover','-s','tests/unit','-p',name])
    save(s/'checks.json',dict(passed=True,cpu_kernels=3,python_test_files=tests))

if __name__=='__main__':main()
