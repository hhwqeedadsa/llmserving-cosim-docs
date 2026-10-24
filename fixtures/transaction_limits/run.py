"""Predeclared functionality; explicit continuation preserves first failed case."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def load(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage','old_backend'):p.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    p.add_argument('--continue-after-source-lease-fix',action='store_true')
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve()
    records=load(stage/'commands.json') if a.continue_after_source_lease_fix else []
    def command(label,tail):
        if a.continue_after_source_lease_fix and any(r.get('label')==label and r['exit_code']==0 for r in records):
            assert load(stage/(label+'-monitor/probe.json'))['status']=='completed'
            return
        cmd=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(stage/(label+'-monitor')),
            '--rss-bytes',str(2**30),'--address-bytes',str(2**31),'--artifact-bytes',str(64*2**20),
            '--artifact-root',str(stage/'network'),'--artifact-root',str(stage/'inputs'),
            '--artifact-root',str(stage/'topology'),'--wall-seconds','60','--min-free-bytes',str(8*2**30),'--',*map(str,tail)]
        done=subprocess.run(cmd,cwd=repo,timeout=65)
        records.append(dict(label=label,command=cmd,cwd=str(repo),exit_code=done.returncode));save(stage/'commands.json',records)
        assert done.returncode==0,label
    if not a.continue_after_source_lease_fix:
        command('prepare',[sys.executable,'-B',repo/'fixtures/transaction_limits/prepare.py','--repository',repo,'--output',stage/'inputs'])
        command('topology',[sys.executable,'-B','-m','moe_ub_cosim','scaleout','topology','--config',stage/'inputs/topology.json','--output',stage/'topology'])
        (stage/'network').mkdir()
    else:
        failure=load(stage/'network/expanded/result.json')
        assert failure['execution_error']=='invalid source read lease identity/budget'
        packets=failure['transactions']['transport']['source_packets']
        assert len(packets)==2 and all(row[0]==0 for row in packets)
    runs=stage/'network'
    cases=[('expanded-corrected','expanded','protocol'),('long-fault-configured','long-recovery','fault')] if a.continue_after_source_lease_fix else [('expanded','expanded','protocol'),('long','long','protocol'),('long-fault','long','fault')]
    for name,work,protocol in cases:
        command(name,[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-transaction-probe',
            '--topology',stage/'topology','--protocol',stage/'inputs'/(protocol+'.json'),
            '--config',stage/'inputs'/(work+'.json'),'--backend',stage/'bin/transaction-probe','--output',runs/name])
        assert load(runs/name/'result.json')['passed']
    command('online',[sys.executable,'-B',repo/'fixtures/transaction_limits/online.py',
        '--topology',stage/'topology','--protocol',stage/'inputs/protocol.json','--work',stage/'inputs/expanded.json',
        '--backend',stage/'bin/transaction-session','--output',runs/'online'])
    for name,backend in [('legacy-old',a.old_backend),('legacy-new',stage/'bin/transaction-probe')]:
        command(name,[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-transaction-probe',
            '--topology',stage/'topology','--protocol',stage/'inputs/protocol.json',
            '--config',repo/'examples/scaleout_rdma_transactions/sparse-windows.json','--backend',backend,'--output',runs/name])
    old=load(runs/'legacy-old/result.json');new=load(runs/'legacy-new/result.json')
    def normalize(r):
        r=dict(r);libs=r.pop('loaded_libraries')
        identities={Path(p).name:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in libs if Path(p).name.startswith('libns3')}
        assert len(identities)==8
        return r,identities
    old_body,old_libs=normalize(old);new_body,new_libs=normalize(new)
    assert old_body==new_body,'legacy result changed beyond library identities'
    assert 'execution_limits' not in new['transactions']
    save(stage/'run.json',dict(functional_runs_completed=True,actual_executions=7,fresh_replay_remaining=1,
        historical_legacy_equal_except_library_paths=True,historical_library_hashes_equal=old_libs==new_libs,
        strong_default_regression_pending=old_libs!=new_libs))


if __name__=='__main__':main()
