"""Optional Linux host phases; never a GPU/ns-3 clock or a scheduling input."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time


class HostTiming:
    def __init__(self,path=None):
        self.file=None;self.stack=[];self.count=0;self.records=0
        if path is not None:
            if 'CLOCK_MONOTONIC' not in time.get_clock_info('monotonic').implementation:
                raise ValueError('host phase traces require Linux CLOCK_MONOTONIC')
            self.file=path.open('x',buffering=1)
            self.write(dict(type='header',schema_version='moe-host-phase-trace/1',writer='python',
                pid=os.getpid(),wall_clock='CLOCK_MONOTONIC',cpu_clock='CLOCK_PROCESS_CPUTIME_ID',unit='ns'))

    def write(self,row):
        self.records+=1
        if self.records>65536:raise ValueError('host timing record budget exceeded')
        self.file.write(json.dumps(row,separators=(',',':'))+'\n')

    @contextmanager
    def span(self,name):
        if self.file is None:
            yield
            return
        self.count+=1;identity=self.count
        self.write(dict(type='begin',id=identity,parent=self.stack[-1] if self.stack else 0,
            name=name,wall_ns=time.monotonic_ns(),cpu_ns=time.process_time_ns()))
        self.stack.append(identity)
        try:
            yield
        finally:
            assert self.stack.pop()==identity
            self.write(dict(type='end',id=identity,wall_ns=time.monotonic_ns(),cpu_ns=time.process_time_ns()))

    def close(self,complete):
        if self.file is None:return
        self.write(dict(type='footer',complete=bool(complete and not self.stack),spans=self.count))
        self.file.close();self.file=None


def read_trace(path):
    """Read a bounded trace or flushed prefix. Never manufacture an open span's end.

    Exclusive durations subtract direct children in the *same* process only.
    A footer certifies this writer's completion, not network/service success.
    """
    path=Path(path)
    def require(ok,message):
        if not ok:raise ValueError(f'{path}: {message}')
    def number(value):return type(value) is int and value>=0
    require(path.stat().st_size<=16*1024*1024,'host trace exceeds 16MiB')
    header=None;footer=None;stack=[];seen=set();completed=[];snapshots=[]
    last_wall=last_cpu=0;records=0;total_bytes=0;trailing_partial=False
    with path.open('rb') as stream:
        for line in stream:
            total_bytes+=len(line);records+=1
            require(total_bytes<=16*1024*1024 and records<=65536,'host trace budget exceeded')
            # A killed writer can leave the final line torn, including valid JSON
            # without its newline. Only fully written records are accepted.
            if not line.endswith(b'\n'):
                require(footer is None,'bytes after host trace footer')
                trailing_partial=True;break
            try:row=json.loads(line)
            except (ValueError,UnicodeDecodeError) as error:
                raise ValueError(f'{path}: malformed host record {records}') from error
            require(type(row) is dict,'host record must be an object')
            if header is None:
                require(row.get('type')=='header' and row.get('schema_version')=='moe-host-phase-trace/1' and
                    row.get('writer') in ('python','native') and number(row.get('pid')) and row['pid']>0 and
                    row.get('wall_clock')=='CLOCK_MONOTONIC' and row.get('cpu_clock')=='CLOCK_PROCESS_CPUTIME_ID' and
                    row.get('unit')=='ns','unsupported host trace header')
                header=row;continue
            require(footer is None,'record after host trace footer')
            kind=row.get('type')
            if kind=='footer':
                require(type(row.get('complete')) is bool and number(row.get('spans')) and
                    row['spans']==len(seen),'invalid host trace footer')
                require(not row['complete'] or not stack,'complete footer with open spans')
                footer=row;continue
            require(kind in ('begin','end','snapshot'),'unknown host record type')
            wall,cpu=row.get('wall_ns'),row.get('cpu_ns')
            require(number(wall) and number(cpu) and wall>=last_wall and cpu>=last_cpu,'non-monotonic host clocks')
            last_wall,last_cpu=wall,cpu
            if kind=='snapshot':
                require(type(row.get('name')) is str and bool(row['name']) and type(row.get('values')) is dict,
                    'invalid host snapshot')
                snapshots.append(row);continue
            identity=row.get('id')
            require(number(identity) and identity>0,'invalid host span id')
            if kind=='begin':
                require(identity not in seen,'duplicate host span id')
                require(number(row.get('parent')) and row['parent']==(stack[-1]['id'] if stack else 0),
                    'host span parent does not match stack')
                require(type(row.get('name')) is str and bool(row['name']),'invalid host span name')
                seen.add(identity);stack.append(dict(row,child_wall_ns=0,child_cpu_ns=0))
            else:
                require(bool(stack) and stack[-1]['id']==identity,'crossing or unmatched host span end')
                begin=stack.pop();wall_delta=wall-begin['wall_ns'];cpu_delta=cpu-begin['cpu_ns']
                require(wall_delta>=begin['child_wall_ns'] and cpu_delta>=begin['child_cpu_ns'],
                    'children exceed host span duration')
                completed.append(dict(id=identity,parent=begin['parent'],name=begin['name'],
                    begin_wall_ns=begin['wall_ns'],end_wall_ns=wall,
                    inclusive_wall_ns=wall_delta,inclusive_cpu_ns=cpu_delta,
                    exclusive_wall_ns=wall_delta-begin['child_wall_ns'],
                    exclusive_cpu_ns=cpu_delta-begin['child_cpu_ns']))
                if stack:
                    stack[-1]['child_wall_ns']+=wall_delta;stack[-1]['child_cpu_ns']+=cpu_delta
    require(header is not None,'no complete host trace header')
    phases={}
    for span in completed:
        phase=phases.setdefault(span['name'],dict(count=0,inclusive_wall_ns=0,inclusive_cpu_ns=0,
            exclusive_wall_ns=0,exclusive_cpu_ns=0))
        phase['count']+=1
        for key in ('inclusive_wall_ns','inclusive_cpu_ns','exclusive_wall_ns','exclusive_cpu_ns'):
            phase[key]+=span[key]
    return dict(header=header,trace_complete=bool(footer and footer['complete']),footer_present=footer is not None,
        trailing_partial_record=trailing_partial,records=records,phases=phases,completed_spans=completed,
        open_spans=[{key:span[key] for key in ('id','parent','name','wall_ns','cpu_ns')} for span in stack],
        snapshots=snapshots)


def check(run,output):
    """Export per-process phase totals, including a failed run's valid prefix."""
    traces={};missing=[]
    for name in ('worker','native'):
        path=Path(run)/f'{name}_host.jsonl'
        if not path.exists():missing.append(name);continue
        trace=read_trace(path);trace.pop('completed_spans');traces[name]=trace
    if not traces:raise ValueError('no host traces; run rdma-run with --host-timing')
    report=dict(schema_version='moe-host-phase-summary/1',passed=True,traces=traces,missing_writers=missing,
        all_traces_complete=not missing and all(t['trace_complete'] for t in traces.values()),
        scope='host observation only; no service success assertion; nested/process wall times must not be added')
    # Exclusive creation preserves any previous analysis, including failures.
    with Path(output).open('x') as stream:json.dump(report,stream,indent=2);stream.write('\n')
    return dict(passed=True,output=str(Path(output).resolve()),all_traces_complete=report['all_traces_complete'],
        missing_writers=missing)
