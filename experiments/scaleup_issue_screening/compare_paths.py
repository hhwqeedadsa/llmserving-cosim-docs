"""Compare actual candidate paths by semantic task and byte range, never UID."""
import argparse
from pathlib import Path

from .workflow import digest, read, save


def identity(row):
    return (row['operation_id'],row['src_rank'],row['dst_rank'],row['message_role'],
            row['message_id'],row['chunk_index'],
            tuple(row['pair_byte_range']),tuple(row['task_byte_range'] or ()))


def compare(baseline, controls, output):
    base=read(baseline/'transfers.json');index={identity(t):t for t in base}
    if len(index)!=len(base):raise ValueError('Duplicate semantic task in baseline')
    result=[]
    for control in controls:
        rows=read(control/'transfers.json');other={identity(t):t for t in rows}
        if len(other)!=len(rows) or not set(index)<=set(other):raise ValueError('Missing fixed baseline tasks')
        changed_tasks=changed_packets=packets=0;changes=[]
        for key,left in index.items():
            right=other[key]
            if left['payload_bytes']!=right['payload_bytes'] or left['sources_sha256']!=right['sources_sha256']:
                raise ValueError('Paired workload changed')
            def paths(t):
                pairs={tuple(tuple(r) for r in p['ranges']):p['ports'] for p in t['path_signatures']}
                if len(pairs)!=t['packets']:raise ValueError('Ambiguous packet byte-range identity')
                return pairs
            a,b=paths(left),paths(right)
            if set(a)!=set(b):raise ValueError('Packetization or application ranges changed')
            count=sum(a[k]!=b[k] for k in a);changed_tasks+=bool(count);changed_packets+=count;packets+=len(a)
            changes.append(dict(operation_id=left['operation_id'],src_rank=left['src_rank'],dst_rank=left['dst_rank'],
                message_role=left['message_role'],pair_byte_range=left['pair_byte_range'],changed_packets=count,
                message_id=left['message_id'],chunk_index=left['chunk_index'],
                packets=len(a),baseline_latency_ns=left['latency_ns'],control_latency_ns=right['latency_ns']))
        result.append(dict(case=control.name,summary_sha256=digest(control/'summary.json'),
            tasks_compared=len(index),tasks_with_changed_paths=changed_tasks,packets_compared=packets,
            packets_with_changed_paths=changed_packets,comparisons=changes))
    save(output,dict(schema_version='issue-screening-actual-path-comparison/1',
        baseline_summary_sha256=digest(baseline/'summary.json'),baseline_tasks=len(index),cases=result,
        identity='operation, source, consumer, message role/ordinal, chunk and byte ranges, with source-token digest checked; packet UIDs not compared across cases',
        boundary='Candidate paths only; random-state equality does not fix paths; input-change total effects do not isolate queueing.'))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--controls',type=Path,nargs='+',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();compare(a.baseline,a.controls,a.output)


if __name__=='__main__':main()
