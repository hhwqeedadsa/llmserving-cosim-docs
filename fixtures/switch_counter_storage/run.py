"""Bounded, no-retry platform acceptance through public commands."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.host_probe import probe
from verify import verify_small,verify,save,load,need


def run(repo,stage,phase):
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    backend=load(stage/'build/backend.json')
    def command(name,args,expected_failure=False,build=False):
        receipt=probe([str(x) for x in args],stage/(name+'-monitor'),rss_bytes=(1 if build else 4)*2**30,
            address_bytes=(2 if build else 8)*2**30,artifact_bytes=2*2**30,wall_seconds=60,
            artifact_roots=[stage],min_free_bytes=8*2**30)
        need(not receipt['remaining_live_group_pids'],'live children')
        need(receipt['status']==('child_failed' if expected_failure else 'completed'),'unexpected command status '+name)
        if expected_failure:need(receipt['child_exit_code']==1,'unexpected failure exit')
    def network(name,scale,protocol,work,binary=None,failed=False):
        command(name,cli+['rdma-stream-probe','--topology',stage/(scale+'-topology'),
            '--protocol',stage/'inputs'/protocol,'--config',stage/'inputs'/work,
            '--backend',binary or backend['stream_probe'],'--output',stage/name],failed)
    if phase=='small':
        command('small-topology',cli+['topology','--config',stage/'inputs/small-topology.json','--output',stage/'small-topology'])
        old=load(repo/'runs/rc_packet_probe_20261027/build/backend.json')['stream_probe']
        need(Path(old).is_file(),'declared old binary missing')
        network('small-old','small','small-default.json','small-work.json',old)
        network('small-default','small','small-default.json','small-work.json')
        for mode in ('legacy_dense','port_dense'):
            network('small-'+mode,'small','small-'+mode+'.json','small-work.json')
        save(stage/'healthy-gate.json',verify_small(stage))
        for mode in ('legacy_dense','port_dense'):
            network('failure-'+mode,'small','failure-'+mode+'.json','failure-work.json',failed=True)
        save(stage/'small-verification.json',verify(stage,False))
    elif phase=='large':
        need(load(stage/'small-verification.json')['passed'],'small gate not passed')
        command('large-topology',cli+['topology','--config',stage/'inputs/large-topology.json','--output',stage/'large-topology'])
        for mode in ('legacy_dense','port_dense'):
            network('large-'+mode,'large','large-'+mode+'.json','large-work.json')
        save(stage/'verification.json',verify(stage))
    elif phase=='native':
        binary=stage/'native-check';lib=stage/'build/output/lib'
        command('native-compile',['/usr/bin/c++','-std=c++20','-O2','-I'+str(stage/'build/output/include'),
            repo/'fixtures/switch_counter_storage/native_check.cc','-L'+str(lib),'-Wl,-rpath,'+str(lib),
            '-lns3.42-point-to-point','-lns3.42-applications','-lns3.42-internet','-lns3.42-network','-lns3.42-core','-o',binary],build=True)
        checks={}
        for case in ('port','legacy','zero','too_many','twice','bad_in','bad_out','bad_priority'):
            child=subprocess.run([binary,case],capture_output=True,timeout=10)
            (stage/('native-'+case+'.stdout')).write_bytes(child.stdout)
            (stage/('native-'+case+'.stderr')).write_bytes(child.stderr)
            checks[case]=child.returncode
            need(child.returncode==0 if case in ('port','legacy') else child.returncode<0,'native initialization guard '+case)
        save(stage/'native-check.json',dict(passed=True,checks=checks,network_executed=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=('small','large','native'))
    p.add_argument('--repo',type=Path,required=True);p.add_argument('--stage',type=Path,required=True)
    a=p.parse_args();run(a.repo.resolve(),a.stage.resolve(),a.phase)
