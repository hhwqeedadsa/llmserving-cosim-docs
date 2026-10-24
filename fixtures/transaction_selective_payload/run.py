"""Seven declared functional networks; the fresh public replay is the eighth."""
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
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();records=[]
    def run(label,cmd):
        argv=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(s/(label+'-monitor')),
            '--wall-seconds','60','--rss-bytes',str(2**30),'--address-bytes',str(2**31),'--artifact-bytes',str(64*2**20),
            '--artifact-root',str(s/'network'),'--artifact-root',str(s/'inputs'),'--artifact-root',str(s/'topology'),
            '--min-free-bytes',str(8*2**30),'--',*map(str,cmd)]
        done=subprocess.run(argv,cwd=repo,timeout=65)
        records.append(dict(label=label,command=argv,cwd=str(repo),exit_code=done.returncode));save(s/'commands.json',records)
        assert done.returncode==0,label
    run('prepare',[sys.executable,'-B',repo/'fixtures/transaction_selective_payload/prepare.py','--repository',repo,'--output',s/'inputs'])
    run('topology',[sys.executable,'-B','-m','moe_ub_cosim','scaleout','topology','--config',s/'inputs/topology.json','--output',s/'topology'])
    (s/'network').mkdir()
    for label,work,protocol in [('request-gap','atomic','request'),('reply-tail','atomic','reply-tail'),('chain','chain','middle'),('explicit','explicit','protocol')]:
        config=s/'inputs'/((protocol+'-protocol.json') if protocol!='protocol' else 'protocol.json')
        run(label,[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-transaction-probe',
            '--topology',s/'topology','--protocol',config,'--config',s/'inputs'/(work+'.json'),
            '--backend',s/'bin/transaction-probe','--output',s/'network'/label])
        assert load(s/'network'/label/'result.json')['passed']
    run('online',[sys.executable,'-B',repo/'fixtures/transaction_selective_payload/online.py',
        '--topology',s/'topology','--protocol',s/'inputs/middle-protocol.json','--work',s/'inputs/online.json',
        '--backend',s/'bin/transaction-session','--output',s/'network/online'])
    for label,backend in [('default-old',a.old_backend),('default-new',s/'bin/transaction-probe')]:
        run(label,[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-transaction-probe',
            '--topology',s/'topology','--protocol',s/'inputs/protocol.json',
            '--config',repo/'examples/scaleout_rdma_transactions/local-io.json','--backend',backend,'--output',s/'network'/label])
    old=load(s/'network/default-old/result.json');new=load(s/'network/default-new/result.json')
    def normalized(r):
        r=dict(r);libraries=r.pop('loaded_libraries')
        r['native_library_hashes']={Path(p).name:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in libraries if Path(p).name.startswith('libns3')}
        assert len(r['native_library_hashes'])==8
        return r
    assert normalized(old)==normalized(new)
    save(s/'run.json',dict(passed=True,network_executions=7,default_complete_result_equal_except_verified_library_paths=True))


if __name__=='__main__':main()
