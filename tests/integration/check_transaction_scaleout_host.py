"""Pure Python/C++ validation and memory checks; never construct a Fabric."""
import argparse
import json
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('build','topology','output'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args();build=args.build.resolve();output=args.output.resolve();output.mkdir(exist_ok=False)
    data=json.loads((build/'build-inputs.json').read_text())
    common=data['commands'][0][:-4]
    # Strip -c source -o object; retain pinned include paths and compiler flags.
    assert data['commands'][0][-4]=='-c'
    records=[]
    def run(command):
        result=subprocess.run(list(map(str,command)),cwd=ROOT,text=True,capture_output=True,timeout=170)
        row=dict(command=list(map(str,command)),exit_code=result.returncode,stdout=result.stdout,stderr=result.stderr)
        records.append(row);(output/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
        if result.returncode:raise RuntimeError('host check failed: '+str(command[0])+': '+result.stderr[-1000:])
    names=['transaction_scaleout_memory','transaction_limits','nic_registration','atomic32_memory','sparse_remote_memory','remote_memory']
    for name in names:
        binary=output/name
        run([data['commands'][0][0],'-std=c++20','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'backend'),ROOT/'tests/unit'/(name+'_test.cc'),'-o',binary])
        run([binary])
    binary=output/'transaction_scaleout_admission'
    # Admission uses the transaction parser only; no Simulator/Fabric execution.
    run([*common,ROOT/'tests/unit/transaction_scaleout_admission_test.cc',
         build/'remote_transactions.o',build/'persistent_streams.o',build/'runtime/lib/libmoe-rdma-fabric.a',
         '-Wl,--no-as-needed',*[build/'runtime/lib'/name for name in data['library_hashes']],'-ldl',
         '-Wl,-rpath,'+str(build/'runtime/lib'),'-o',binary])
    run([binary,args.topology.resolve()/'topology.json',ROOT/'examples/scaleout_rdma_transactions/scaleout-rdma.json',
         ROOT/'examples/scaleout_rdma_transactions/scaleout-profile.json'])
    patterns=['transaction_scaleout_profile','scaleout_transactions','scaleout_transaction_limits','scaleout_transaction_selective',
              'memory_registrations','self_peer_service','context_transactions','transaction_effect_order',
              'transaction_session_alarms','native_proof_coverage','static_stream_validation_cache']
    suite=unittest.TestSuite()
    for name in patterns:suite.addTests(unittest.defaultTestLoader.discover(str(ROOT/'tests/unit'),pattern=name+'_test.py'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    summary=dict(passed=result.wasSuccessful(),cpp_programs=len(names)+1,python_methods=result.testsRun,
                 pure_checks=True,new_networks=0,commands=records)
    (output/'verification.json').write_text(json.dumps(summary,indent=2)+'\n')
    if not result.wasSuccessful():raise SystemExit(1)
    print(json.dumps({k:v for k,v in summary.items() if k!='commands'}))


if __name__=='__main__':main()
