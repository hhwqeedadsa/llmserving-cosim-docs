"""Independent complete stdout and final-MiB checks of the partial audit."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--summary',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    args=p.parse_args();assert not args.output.exists()
    data=args.summary.read_bytes();summary=json.loads(data);checked={}
    regex=re.compile(rb'^(issue|callback),sys->id=(\d+), tick=(\d+), node->id=(\d+), '
                     rb'node->name=([^,]+), node->type=(\d+), graph_id=(\S+)\n$')
    for name,a in summary['cases'].items():
        root=Path(a['root']);stdout=(root/'native.stdout.log').read_bytes()
        assert hashlib.sha256(stdout).hexdigest()==a['stdout_sha256']
        events=[json.loads(line[len(b'MOE_RDMA_EVENT '):]) for line in stdout.splitlines()
                if line.startswith(b'MOE_RDMA_EVENT ')]
        assert events==a['decisions'] and len(events)==222
        completed={c['graph_id']:c['finished_ns'] for e in events for c in e['completed']}
        assert len(completed)==221 and all(i.endswith('.w0') for i in completed)
        assert [e['decision_id'] for e in events]==list(range(222))
        path=root/'log/astra_nodes.log'
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size-2**20));raw=stream.read(2**20)
        assert hashlib.sha256(raw).hexdigest()==a['tail_sha256']
        assert raw==(args.summary.parent/(name+'-tail.bin')).read_bytes()
        parsed=[m for line in raw.splitlines(keepends=True)[1:] if (m:=regex.fullmatch(line))]
        assert len(parsed)==a['tail_complete_node_records']
        m=parsed[-1];kind,rank,tick,node,label,node_type,graph=m.groups()
        expected=dict(kind=kind.decode(),rank=int(rank),tick=int(tick),node=int(node),
                      name=label.decode(),node_type=int(node_type),graph_id=graph.decode())
        assert expected==a['tail_last_complete_node_record']
        assert max(int(m[3]) for m in parsed)==a['tail_max_tick']
        assert not raw.endswith(b'\n') and not a['result_exists']
        checked[name]=dict(saved_notifications=221,tail_node_records=len(parsed),
                           max_recorded_tick=a['tail_max_tick'])
    waves=summary['baseline_recovered_wave_prefixes']
    first=[v for v in waves.values() if v['step']==0]
    second=[v for v in waves.values() if v['step']==1]
    assert len(first)==len(second)==256
    assert all(v['issue']==v['callback'] and v['max_callback_layer']==47 for v in first)
    assert all(set(map(int,v['ranks']))==set(summary['cases']['baseline']['input']['parallel']['groups'][v['group']])
               for v in waves.values())
    assert all(v['max_issued_layer']==47 for v in second)
    assert min(v['max_callback_layer'] for v in second)==8
    assert max(v['max_callback_layer'] for v in second)==11
    meta=json.loads((args.summary.parent/'historical_source.json').read_text())
    source=subprocess.check_output(['git','show',meta['commit']+':'+meta['path']])
    assert hashlib.sha256(source).hexdigest()==meta['sha256']
    assert source==(args.summary.parent/'historical_scaleout_service.py').read_bytes()
    assert b"(output/'native.stdout.log').open('w') as logs:" in source
    result=dict(passed=True,summary_sha256=hashlib.sha256(data).hexdigest(),cases=checked,
        historical_source_verified=True,baseline_first_step_callback_balanced_waves=256,
        scope='independent stdout/tail/source identity; recovered whole-log counts checked internally, not independently re-enumerated',
        new_simulation_executed=False,full_audit_complete=False)
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
