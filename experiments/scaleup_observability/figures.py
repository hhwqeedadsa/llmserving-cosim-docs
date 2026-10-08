"""Optional standalone research figure from verified first-batch overviews."""
import csv
import os
from pathlib import Path
import sys

from .common import digest, read, require, save
from .workflow import verify_complete


def render(batch, output):
    batch, output = Path(batch).resolve(), Path(output).resolve()
    os.environ['MPLCONFIGDIR'] = str(output/'matplotlib-cache')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    rows = []
    for case in read(batch/'batch.json')['cases']:
        _, summary = verify_complete(batch,case)
        folder = batch/'runs'/case['case_id']/'overview'
        with (folder/'ports.csv').open() as f:
            ports = list(csv.DictReader(f))
        with (folder/'transfer-groups.csv').open() as f:
            selected = [r for r in csv.DictReader(f) if r['operation']=='LOAD' and
                r['kind']=='combine' and r['message_role']=='data' and
                r['locality']=='cross_server' and int(r['payload_bytes'])==2048]
        require(len(selected)==1, 'Expected one comparable 2048 B cross-server LOAD group')
        rows.append(dict(case_id=case['case_id'], input_set=case['input_set'],
            request_count=case['request_count'], round_us=summary['round_ns']/1000,
            single_round_million_tokens_per_second=summary['decode_tokens_per_second']/1e6,
            max_port_busy_percent=100*max(float(p['link_busy_fraction']) for p in ports),
            cross_server_load_p95_ns=float(selected[0]['task_latency_ns_p95']),
            overview_sha256=digest(folder/'summary.json')))
    require(len(rows)==9 and len({(r['input_set'],r['request_count']) for r in rows})==9,
            'Nine distinct normal cases required')
    plt.rcParams.update({'font.size':10,'svg.hashsalt':'scaleup-normal-v1'})
    fig, axes = plt.subplots(2,2,figsize=(10,6.6),layout='constrained')
    panels = [('round_us','Complete Decode','Duration (us)'),
              ('single_round_million_tokens_per_second','Single-round completion rate','Million tokens/s'),
              ('max_port_busy_percent','Busiest switch egress','Transmit time / run time (%)'),
              ('cross_server_load_p95_ns','2048 B cross-server LOAD','Task latency p95 (ns)')]
    colors = ['#0072B2','#D55E00','#009E73']
    for ax,(field,title,label) in zip(axes.flat,panels):
        for seed,color in enumerate(colors):
            values=sorted((r for r in rows if r['input_set']==seed),key=lambda r:r['request_count'])
            ax.plot([r['request_count'] for r in values],[r[field] for r in values],
                    marker='o',color=color,label=f'Input set {seed}')
        ax.set(title=title,xlabel='Total active requests',ylabel=label,xticks=[16,32,64])
        ax.grid(alpha=.25)
        ax.set_ylim(0,max(r[field] for r in rows)*1.12 or 1)
    axes[0,0].legend(frameon=False)
    fig.suptitle('Normal Scale-Up runs: 8 ranks, one Decode, fixed device/protocol settings\n'
                 'Three independent input sets; nested sizes within each set; reference compute costs',fontsize=11)
    fig.savefig(output/'normal-overview.svg',metadata={'Date':None})
    fig.savefig(output/'normal-overview.png',dpi=180)
    plt.close(fig)
    save(output/'plot-data.json',dict(schema_version='scaleup-normal-figure/1',rows=rows,
        matplotlib_version=matplotlib.__version__,
        note='Descriptive comparisons only; single-round completion rate is not steady-state throughput.',
        figures={name:digest(output/name) for name in ('normal-overview.svg','normal-overview.png')}))


if __name__=='__main__':render(*sys.argv[1:])
