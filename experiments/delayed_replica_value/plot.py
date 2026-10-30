"""Plot saved timing and accepted-byte curves using public read-only export."""
import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('frozen','analysis','normal','local','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();f=json.loads(a.frozen.read_bytes());analysis=json.loads(a.analysis.read_bytes());out=a.output.resolve()
    out.mkdir(parents=True,exist_ok=False);plt.rcParams.update({'font.size':10,'svg.fonttype':'none'})
    cases=analysis['cases'];colors=['#466c90','#bb7736','#248575']
    fig,axes=plt.subplots(1,2,figsize=(11,3.8),sharex=True,constrained_layout=True)
    for ax,condition in zip(axes,('normal','local')):
        for y,(policy,color) in enumerate(zip(('single','eager','delayed'),colors)):
            r=cases[policy+'_'+condition];ready=r['result_ready_ns']/1000;end=r['attempts_finished_ns']/1000;drain=r['network_drained_ns']/1000
            ax.barh(y,ready,height=.48,color=color)
            ax.plot([ready,end],[y,y],color='#687387',lw=4)
            ax.plot([end,drain],[y,y],color='#a0a7b3',lw=2,ls=':')
            ax.plot(end,y,'|',color='#202c40',ms=12);ax.text(ready+.8,y-.26,f'{ready:.3f}',fontsize=9)
        ax.set_yticks(range(3),['Single','Immediate replica','Delayed (70 us)']);ax.invert_yaxis()
        ax.set_title('Normal' if condition=='normal' else 'Local transient loss');ax.set_xlabel('Simulation time (us)')
        ax.set_xlim(0,155);ax.grid(axis='x',alpha=.2);ax.set_axisbelow(True)
    fig.suptitle('Finite MoE segment: result ready, attempt cleanup, network drain')
    for extension in ('png','svg'):fig.savefig(out/('latency-comparison.'+extension),dpi=180)
    plt.close(fig)
    slices=[('single-primary',Path(f['references']['single_local']['directory']),'g0.w0',colors[0]),
            ('delayed-primary',a.local.resolve(),'g0.w0',colors[1]),
            ('delayed-replica',a.local.resolve(),'g1.w0',colors[2])]
    fig,ax=plt.subplots(figsize=(9,4),constrained_layout=True);summaries={}
    for name,run,graph,color in slices:
        target=out/name
        subprocess.run([sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-arrivals','--run',str(run),
            '--output',str(target),'--graph',graph,'--layer','0','--phase','dispatch'],check=True)
        with (target/'arrival_curve.csv').open() as stream:rows=list(csv.DictReader(stream))
        summary=json.loads((target/'summary.json').read_bytes());summaries[name]=summary
        times=[int(row['time_ns'])/1000 for row in rows];accepted=[int(row['accepted_bytes']) for row in rows]
        assert times and accepted[-1]>0
        ax.step([0,*times,155],[0,*[100*v/accepted[-1] for v in accepted],100],where='post',label=name,color=color,
            lw=2,ls='--' if name=='delayed-primary' else '-')
    ax.axvline(70,color='#687387',lw=1,ls=':',label='Replica launch: 70 us')
    ax.set(xlim=(0,155),ylim=(0,104),xlabel='Absolute simulation time (us)',ylabel='Accepted unique payload (%)',
        title='Local-loss slice: first-layer dispatch, packet-derived acceptance')
    ax.grid(alpha=.2);ax.legend(loc='lower right',fontsize=9)
    for extension in ('png','svg'):fig.savefig(out/('dispatch-arrivals.'+extension),dpi=180)
    plt.close(fig)
    (out/'slice-summaries.json').write_text(json.dumps(summaries,indent=2)+'\n')
    print(json.dumps(dict(passed=True,new_network_executions=0,figures=2,public_slice_exports=3)))

if __name__=='__main__':main()
