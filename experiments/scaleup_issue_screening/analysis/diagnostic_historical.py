"""Related historical review using only identifiable aligned-window bounds.

Old records lack exact nonempty/busy/credit time. Do not fill those fields with
zeros or claim the full diagnostic evaluator can run on them.
"""
import argparse
from collections import Counter,defaultdict
import gzip
import json
from pathlib import Path
import sys
from .workflow import CONFIG,digest,host,read,save


def bounds(area,voq_peak,fifo_peak):
    upper=voq_peak+fifo_peak
    if area and not upper:raise ValueError('Positive integral with no possible queue peak')
    return dict(peak_lower=max(voq_peak,fifo_peak),peak_upper=upper,
                nonempty_time_lower_ns=(area+upper-1)//upper if upper else 0)


def review(normal_root,rule_path,output):
    normal_root,output=Path(normal_root),Path(output)
    if output.exists():raise ValueError('Historical review output must be new')
    rule=read(rule_path);results={}
    for name in rule['historical_aggregate_review']:
        directory=normal_root/name;summary=read(directory/'summary.json');meta=read(directory/'ports.json')
        if summary['scope_ns']!=[0,1000000] or summary['rule']['scope_start_ns']!=0 or summary['rule']['scope_end_ns']!=1000000:
            raise ValueError('Historical coverage differs')
        ports={(p['switch_id'],p['port_id']):p for p in meta};data={k:{} for k in ports}
        with gzip.open(directory/'windows.jsonl.gz','rt') as stream:
            for line in stream:
                row=json.loads(line);k=row['switch_id'],row['port_id'];i=row['start_ns']//1000
                if row['start_ns']%1000 or row['end_ns']-row['start_ns']!=1000 or not 0<=i<1000 or i in data[k]:raise ValueError('Invalid historical aligned window')
                data[k][i]=row
        by_period={}
        for period in rule['periods_us']:
            counts=Counter();by_role=defaultdict(Counter)
            for key,p in ports.items():
                for start in range(0,1000,period):
                    found=[data[key][i] for i in range(start,min(1000,start+period)) if i in data[key]]
                    area=sum(r['voq_byte_ns']+r['fifo_byte_ns'] for r in found)
                    b=bounds(area,max((r['voq_peak'] for r in found),default=0),max((r['fifo_peak'] for r in found),default=0))
                    state=('definite_queue_window' if b['peak_lower']>=rule['queue_peak_bytes'] and b['nonempty_time_lower_ns']>=rule['minimum_window_wait_ns'] else
                        'cannot_meet_queue_rule' if not area or b['peak_upper']<rule['queue_peak_bytes'] else 'unknown_without_exact_joint_state')
                    counts[state]+=1;by_role[p['role']][state]+=1
            by_period[str(period)]=dict(windows=dict(counts),roles={k:dict(v) for k,v in by_role.items()})
        pairs=read(directory/'plane-pairs.json')
        # Preserve the existing paired work amounts and t90 definition. The
        # interpretation remains arrival timing versus service, never a label.
        comparable=sum(p['relative_work_difference']<=rule['peer_work_relative_difference'] and
            p['t90_difference_ns']>=rule['peer_progress_gap_ns'] for p in pairs)
        results[name]=dict(archive_sha256=summary['archive_sha256'],
            input_sha256={n:digest(directory/n) for n in ('summary.json','ports.json','windows.jsonl.gz','plane-pairs.json')},
            ports=len(ports),periods_us=by_period,comparable_progress_differences=comparable,
            unsupported=['exact continuous queue events','actual serialization busy time','actual CBFC checks','1024ns histogram phase'],
            scope='Related frozen-rule bound review, not independent accuracy or zero blocked-credit evidence')
    save(output,dict(schema_version='issue-diagnostic-historical-review/1',rule_sha256=digest(rule_path),cases=results,program_sha256=digest(__file__)))
    print('Reviewed',len(results),'historical scales with explicit missing fields')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--normal-root',required=True,type=Path)
    p.add_argument('--rule',type=Path,default=CONFIG/'observation/diagnostic-rule.json');p.add_argument('--output',required=True,type=Path)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:review(a.normal_root,a.rule,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],str(a.output)+'-resources',**host())
        if r['status']!='completed':raise SystemExit(r['status'])

if __name__=='__main__':main()
