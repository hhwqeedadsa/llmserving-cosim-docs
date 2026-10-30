"""Plot timing and measured shared-port activity from saved packet evidence."""
import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def save_figure(fig,out,name):
    fig.savefig(out/(name+'.png'),dpi=180)
    path=out/(name+'.svg');fig.savefig(path,metadata={'Date':None})
    path.write_text('\n'.join(line.rstrip() for line in path.read_text().splitlines())+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();analysis=json.loads((args.analysis/'analysis.json').read_bytes())
    assert analysis['passed'];out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    plt.rcParams.update({'font.size':10,'svg.fonttype':'none','svg.hashsalt':'multicall_externality/1'})
    cases=analysis['cases'];colors=['#52758e','#278575'];fig,axes=plt.subplots(1,2,figsize=(11,4.1),sharex=True,constrained_layout=True)
    for ax,condition in zip(axes,('normal','local')):
        labels=[]
        for i,policy in enumerate(('control','treatment')):
            case=cases[policy+'-'+condition]
            for j,call in enumerate(('target','healthy')):
                y=3*i+j;end=case['result_ready_ns'][call]/1000
                ax.barh(y,end,height=.65,color=colors[j],alpha=.65 if policy=='control' else 1)
                ax.text(end+1,y,f'{end:.3f}',va='center',fontsize=9)
                labels.append((y,policy+' / '+call))
            ax.plot(case['network_drained_ns']/1000,3*i,'|',color='#343f50',ms=17)
        ax.set_yticks([v[0] for v in labels],[v[1] for v in labels]);ax.invert_yaxis()
        ax.set(title='Normal' if condition=='normal' else 'Local transient loss',xlabel='Simulation time (us)',xlim=(0,155))
        ax.grid(axis='x',alpha=.18);ax.set_axisbelow(True)
    fig.suptitle('128-endpoint Clos: whole-call result ready (bar), network drain (tick)')
    save_figure(fig,out,'completion')
    plt.close(fig)
    local=cases['treatment-local'];ports={tuple(p) for p in local['shared_backup_healthy_ports']}
    with (args.analysis/'treatment-local-switch-residence.csv').open() as stream:rows=list(csv.DictReader(stream))
    # Each point is a real outgoing DATA packet on a shared egress port.
    traffic={g:[int(r['tx_ns'])/1000 for r in rows if int(r['group'])==g and (int(r['node']),int(r['port'])) in ports] for g in (1,2)}
    assert traffic[1] and traffic[2]
    healthy=local['result_ready_ns']['healthy']/1000
    assert max(traffic[2])<healthy<70<local['first_backup_source_tx_ns']/1000<local['first_backup_switch_rx_ns']/1000
    bins=[i/2 for i in range(281)]
    fig,axes=plt.subplots(2,1,figsize=(10,5.8),constrained_layout=True,gridspec_kw={'height_ratios':[2,1]})
    ax=axes[0]
    for group,color,label in ((2,colors[1],'Healthy primary'),(1,'#b27840','Target replica')):
        ax.hist(traffic[group],bins=bins,histtype='step',lw=1.6,color=color,label=label)
    ax.axvline(70,color='#5b6575',ls=':',lw=1,label='Replica launch')
    ax.set(xlim=(0,140),xlabel='Simulation time (us)',ylabel='DATA egress events / 0.5 us',
        title=f'Local treatment: DATA on {len(ports)} shared switch egress ports')
    ax.grid(alpha=.15);ax.legend()
    ax=axes[1]
    points=[(healthy,'Healthy result',colors[1]),(70,'Replica launch','#5b6575'),
        (local['first_backup_source_tx_ns']/1000,'First replica source TX','#b27840'),
        (local['first_backup_switch_rx_ns']/1000,'First replica switch RX','#8c5730')]
    for y,(value,label,color) in enumerate(points):
        ax.scatter(value,y,color=color,s=45,zorder=3);ax.text(value+.035,y,f'{value:.3f} us',va='center',fontsize=9)
    ax.set_yticks(range(4),[p[1] for p in points]);ax.invert_yaxis()
    ax.set(xlim=(69.7,72.9),xlabel='Simulation time (us)',title='Shared ports exist, but the two DATA windows do not overlap')
    ax.grid(axis='x',alpha=.18)
    save_figure(fig,out,'shared-port-activity')
    plt.close(fig)
    print(json.dumps(dict(passed=True,figures=2,shared_ports=len(ports),new_network_executions=0)))


if __name__=='__main__':main()
