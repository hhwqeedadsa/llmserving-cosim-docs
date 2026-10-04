"""Compare real UB results across idle scans, auxiliary traces and gzip buffers."""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import subprocess
import time

from moe_ub_cosim.record_storage import iter_result,iter_port_records


def read(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('baseline','old-backend','backend','output-dir'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();old=args.baseline.resolve();out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    command=read(old/'manifest.json')['command'];protocol=[json.loads(s) for s in (old/'protocol.jsonl').read_text().splitlines()]
    commits=''.join(json.dumps(r)+'\n' for r in protocol if r['type']=='COMMIT')
    expected_protocol=[r for r in protocol if r['type']=='DECIDE']
    assert (Path(command[4])/'backend.json').is_file()
    shared=out/'shared';shared.mkdir()
    shutil.copyfile(old/'deployment.json',shared/'deployment.json')
    for name in ('bootstrap','graphs'):shutil.copytree(old/name,shared/name)
    expected=None;expected_ports=None;results={}
    cases=[('original',args.old_backend,False,2**20),('idle-scan',args.backend,False,2**20),
           ('auxiliary-off',args.backend,True,2**20),('buffer-64m',args.backend,True,64*2**20)]
    for label,binary,off,buffer_size in cases:
        directory=out/label;directory.mkdir();network=directory/'network';shutil.copytree(command[4],network)
        backend=read(network/'backend.json')
        if off:
            for key in ('UB_TASK_TRACE_ENABLE','UB_PACKET_TRACE_ENABLE','UB_PORT_TRACE_ENABLE'):
                backend['globals'][key]='false'
                backend['origins']['globals'][key].append(dict(source='explicit_resource_test_auxiliary_diagnostics',value='false'))
            save(network/'backend.json',backend)
        policy=dict(schema_version='moe-ub-record-storage/1',max_compressed_bytes=128*2**20,
            max_buffer_bytes=buffer_size,max_record_bytes=2**18)
        save(shared/'record-storage.json',policy)
        argv=list(command);argv[0]=str(binary.resolve());argv[2]=str(shared);argv[4]=str(network);argv[6]=str(directory/'execution')
        start=time.monotonic();started=datetime.now(timezone.utc).isoformat()
        process=subprocess.run(argv,input=commits,text=True,capture_output=True,timeout=180)
        elapsed=time.monotonic()-start
        for name in ('stdout','stderr'):(directory/(name+'.log')).write_text(getattr(process,name))
        (directory/'stdin.jsonl').write_text(commits)
        save(directory/'command.json',dict(argv=argv,cwd=str(Path.cwd()),started_utc=started,
            ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=process.returncode,wall_seconds=elapsed,record_policy=policy))
        assert process.returncode==0,process.stderr[-3000:]
        assert [json.loads(s) for s in process.stdout.splitlines()]==expected_protocol
        execution=directory/'execution';report=read(execution/'result.json')
        assert report['normal_exit'] and report['network_drain']['idle'] and report['network_initializations']==1
        expanded=deepcopy(report)
        for name in report['record_storage']['fields']:expanded[name]=list(iter_result(execution,name,report))
        expanded.pop('record_storage')
        expanded['loaded_libraries']=sorted(Path(p).name for p in expanded['loaded_libraries'])
        port_rows={}
        for port in (execution/'ports').iterdir():
            fields=read(port/'record-storage.json')['fields']
            port_rows[port.name]={name:list(iter_port_records(execution,port,name)) for name in fields}
            for name in ('observation-audit.json','resource-audit.json','observed/public_config.json'):
                port_rows[port.name][name]=read(port/name)
        if expected is None:expected=expanded;expected_ports=port_rows
        else:assert expanded==expected and port_rows==expected_ports,label
        native=list((execution/'runlog').glob('*.tr'))
        assert bool(native)!=off
        effective=read(execution/'effective_backend.json')
        assert effective['globals']==backend['globals']
        results[label]=dict(wall_seconds=elapsed,compressed_bytes=report['record_storage']['compressed_bytes'],
            peak_buffered_payload_bytes=report['record_storage']['peak_buffered_payload_bytes'],
            native_auxiliary_bytes=sum(p.stat().st_size for p in native),
            finished_ns=report['service_finished_ns'],writes=len(expanded['transfers']),
            research_ports=len(port_rows),normal_exit=True)
    save(out/'summary.json',dict(passed=True,cases=results,protocols_identical=True,
        execution_records_identical=True,all_raw_research_port_records_identical=True,
        target_scale_executed=False))
    print('PASS: unchanged execution, provenance, queue/packet records and drain with bounded runtime costs')


if __name__=='__main__':main()
