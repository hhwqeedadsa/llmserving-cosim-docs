"""Check application-window admission through CLI and direct native probe."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys


def cases(original):
    out={}
    def add(name,edit):
        w=deepcopy(original);edit(w);out[name]=w
    for field in ('application_window_model','application_windows'):
        add('missing-'+field,lambda w,f=field:w.pop(f))
    add('bad-mode',lambda w:w['application_window_model'].update(mode='revoke/1'))
    add('unknown-field',lambda w:w['application_windows'][0].update(extra=1))
    add('duplicate-view',lambda w:w['application_windows'][1].update(id=1))
    add('unknown-backing',lambda w:w['application_windows'][0].update(window_key=99))
    add('range',lambda w:w['application_windows'][1].update(bytes=65))
    add('boolean-id',lambda w:w['application_windows'][1].update(id=True))
    add('foreign-use',lambda w:w['operations'][1].update(application_uses=[1]))
    add('duplicate-use',lambda w:w['operations'][1].update(application_uses=[11,11]))
    add('missing-use',lambda w:w['operations'][3].pop('application_uses'))
    add('closed-use',lambda w:w['operations'][1].update(application_uses=[10]))
    add('duplicate-close',lambda w:w['operations'][2].update(release_window=10))
    add('close-in-use',lambda w:w['operations'][2].update(after=[]))
    add('applied-is-not-completed',lambda w:w['operations'][4]['after'][0].update(event='applied'))
    add('acked-is-not-completed',lambda w:w['operations'][4]['after'][0].update(event='request_acked'))
    add('foreign-close',lambda w:w['operations'][2].update(rank=0))
    add('close-with-use',lambda w:w['operations'][2].update(application_uses=[11]))
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('topology','protocol','work','backend','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();a.output.mkdir(exist_ok=False);reports=[]
    for name,work in cases(json.loads(a.work.read_text())).items():
        config=a.output/(name+'.json');config.write_text(json.dumps(work,indent=2)+'\n')
        for mode in ('cli','native'):
            target=a.output/(name+'-'+mode)
            cmd=([sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-transaction-probe','--topology',str(a.topology),
                  '--protocol',str(a.protocol),'--config',str(config),'--backend',str(a.backend),'--output',str(target)]
                 if mode=='cli' else [str(a.backend),str(a.topology/'topology.json'),str(a.protocol),str(config),str(target)])
            r=subprocess.run(cmd,capture_output=True,timeout=10)
            (a.output/(name+'-'+mode+'.stdout')).write_bytes(r.stdout);(a.output/(name+'-'+mode+'.stderr')).write_bytes(r.stderr)
            if r.returncode==0 or target.exists() or not r.stderr:raise AssertionError(name+'/'+mode)
            reports.append(dict(case=name,mode=mode,command=cmd,exit_code=r.returncode,output_created=False))
    (a.output/'verification.json').write_text(json.dumps(dict(schema_version='application-windows-reject/1',checks=reports),indent=2)+'\n')
    print(f'{len(reports)} CLI/native rejections without output allocation')


if __name__=='__main__':main()
