"""Measured whole-call timings and overlapping DATA/EP join delays."""
import argparse,csv,json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def save(fig,out,name):
 fig.savefig(out/(name+'.png'),dpi=180)
 p=out/(name+'.svg');fig.savefig(p,metadata={'Date':None})
 p.write_text('\n'.join(s.rstrip() for s in p.read_text().splitlines())+'\n');plt.close(fig)


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--analysis',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
 a=p.parse_args();r=json.loads((a.analysis/'analysis.json').read_bytes());assert r['passed'];a.output.mkdir(parents=True,exist_ok=False)
 plt.rcParams.update({'font.size':10,'svg.fonttype':'none','svg.hashsalt':'sequential_externality/1'})
 colors=['#52758e','#278575'];fig,axes=plt.subplots(1,2,figsize=(11.5,4.2),sharex=True,constrained_layout=True)
 for ax,condition in zip(axes,('normal','local')):
  labels=[]
  for i,policy in enumerate(('control','treatment')):
   case=r['cases'][policy+'-'+condition]
   for j,call in enumerate(('target','healthy')):
    y=3*i+j;end=case['result_ready_ns'][call]/1000
    ax.barh(y,end,height=.65,color=colors[j],alpha=.65 if policy=='control' else 1)
    ax.text(end+1,y,f'{end:.3f}',va='center',fontsize=9);labels.append((y,policy+' / '+call))
   ax.plot(case['network_drained_ns']/1000,3*i,'|',color='#343f50',ms=17)
  ax.set_yticks([v[0] for v in labels],[v[1] for v in labels]);ax.invert_yaxis()
  ax.set(title='Normal' if condition=='normal' else 'Local transient loss',xlabel='Simulation time (us)',xlim=(0,225))
  ax.grid(axis='x',alpha=.18);ax.set_axisbelow(True)
 fig.suptitle('128-endpoint Clos, two Decode steps: whole-call result (bar), network drain (tick)')
 save(fig,a.output,'completion')
 local=r['cases']['treatment-local'];ports={tuple(v) for v in local['shared_backup_healthy_ports']}
 with (a.analysis/'treatment-local-switch-residence.csv').open() as stream:rows=list(csv.DictReader(stream))
 traffic={g:[int(v['tx_ns'])/1000 for v in rows if int(v['group'])==g and (int(v['node']),int(v['port'])) in ports] for g in (1,2)}
 fig,axes=plt.subplots(2,1,figsize=(10.5,6.6),constrained_layout=True)
 ax=axes[0]
 for group,color,label in ((2,colors[1],'Healthy primary'),(1,'#b27840','Target replica')):
  ax.hist(traffic[group],bins=[i/2 for i in range(441)],histtype='step',lw=1.5,color=color,label=label)
 ax.axvline(70,color='#5b6575',ls=':',lw=1,label='Replica launch')
 ax.set(xlim=(0,220),xlabel='Simulation time (us)',ylabel='DATA egress events / 0.5 us',title=f'Actual DATA activity on {len(ports)} common switch egress ports')
 ax.grid(alpha=.15);ax.legend(fontsize=9)
 ax=axes[1];names=('L0.dispatch','L0.combine','L1.dispatch','L1.combine')
 for offset,condition,color in ((-.18,'normal','#52758e'),(.18,'local','#b27840')):
  values={v['gate_id']:v['change_ns'] for v in r['effects'][condition]['healthy_phase_release_changes'] if v['graph_id']=='g2.w1'}
  ax.bar([i+offset for i in range(4)],[values[v] for v in names],width=.34,color=color,label=condition)
  for i,name in enumerate(names):ax.text(i+offset,values[name]+20,str(values[name]),ha='center',fontsize=9)
 ax.set_xticks(range(4),names);ax.set(ylabel='Gate release change (ns)',ylim=(0,1450),title='Healthy second Decode: actual whole-EP gate delays, treatment minus control')
 ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True);ax.legend()
 save(fig,a.output,'overlap-and-gates')
 print(json.dumps(dict(passed=True,figures=2,new_network_executions=0)))


if __name__=='__main__':main()
