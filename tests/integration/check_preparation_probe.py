"""The diagnostic executable uses the real installer and stops before activation."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess

from moe_ub_cosim.service import read_line
from moe_ub_cosim.record_storage import iter_result


def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    old=args.baseline.resolve();shared=out/'shared';shared.mkdir()
    shutil.copyfile(old/'deployment.json',shared/'deployment.json')
    shutil.copytree(old/'bootstrap',shared/'bootstrap')
    shutil.copytree(old/'graphs/g0',shared/'graphs/g0')
    save(shared/'record-storage.json',dict(schema_version='moe-ub-record-storage/1',
        max_compressed_bytes=2**20,max_buffer_bytes=2**20,max_record_bytes=2**18))
    with (old/'protocol.jsonl').open() as stream:
        initial=json.loads(next(stream));commit=json.loads(next(stream))
    assert len(commit['submissions'])==1 and commit['submissions'][0]['graph_id']=='g0'
    plan=read(shared/'graphs/g0/plan.json')
    command=read(old/'manifest.json')['command']
    command[0]=str(args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service-resource-probe')
    command[2]=str(shared);command[6]=str(out/'execution')
    started=datetime.now(timezone.utc).isoformat()
    child=subprocess.run(command,input=json.dumps(commit)+'\n',capture_output=True,text=True,timeout=120)
    for name in ('stdout','stderr'):(out/(name+'.log')).write_text(getattr(child,name))
    save(out/'command.json',dict(argv=command,cwd=str(Path.cwd()),started_utc=started,
        ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=child.returncode))
    assert child.returncode==0,child.stderr[-4000:]
    assert [json.loads(line) for line in child.stdout.splitlines()]==[initial]
    result=read(out/'execution/resource-preparation.json')
    assert result['preparation_completed'] and result['simulation_ns']==0
    assert not result['business_activated'] and not result['normal_service_exit_verified']
    assert result['fabric_transfers']==sum('ub_task_id' in t for t in plan['transfers'])
    assert result['local_transfers']==sum('ub_task_id' not in t for t in plan['transfers'])
    assert result['validated_nodes']==sum(len(g['nodes']) for g in plan['rank_graphs'])
    assert result['timing_nodes']==0 and not (out/'execution/result.json').exists()
    assert result['archived_transfer_data'] and result['retained_transfer_descriptors']==0
    # The production trace option must leave the real one-graph execution and
    # all raw logical result records unchanged. There is no diagnostic _Exit.
    reports=[];commit.update(next_wakeup_ns=None,input_exhausted=True)
    for trace in (False,True):
        directory=out/('traced' if trace else 'untraced');command[6]=str(directory)
        command[0]=str(args.build_dir.resolve()/'output/cosim/ns3.44-cosim-service')
        environment=dict(os.environ);environment['MOE_UB_HOST_PREPARATION_TRACE']='1' if trace else '0'
        with (out/(directory.name+'.stderr.log')).open('x') as errors:
            child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,env=environment,bufsize=0)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(child.stdout,selectors.EVENT_READ);buffer=bytearray()
                    first=json.loads(read_line(child,selector,buffer,60));assert first==initial
                    child.stdin.write((json.dumps(commit)+'\n').encode());child.stdin.flush()
                    done=json.loads(read_line(child,selector,buffer,60))
                    assert done['decision_id']==1 and len(done['completed'])==1
                    final=dict(commit,decision_id=1,now_ns=done['now_ns'],submissions=[],service_finished=True)
                    child.stdin.write((json.dumps(final)+'\n').encode());child.stdin.close()
                    assert child.wait(timeout=60)==0 and not buffer and child.stdout.read()==b''
                report=read(directory/'result.json');assert report['normal_exit'] and report['network_drain']['idle']
                assert (directory/'resource-preparation.json').exists()==trace
                if trace:
                    stages=[json.loads(line) for line in (directory/'preparation-phases.jsonl').read_text().splitlines()]
                    retired=[row for row in stages if row['stage']=='graph_retired_before_frontend_handoff']
                    assert len(retired)==1 and retired[0]['simulation_ns']==done['now_ns']
                    heap=retired[0]['host_heap']
                    assert heap['allocator']=='glibc' and heap['arena_bytes']==heap['allocated_arena_bytes']+heap['free_arena_bytes']
                expanded={field:list(iter_result(directory,field,report)) for field in report['record_storage']['fields']}
                report.pop('record_storage');reports.append((report,expanded))
                save(out/(directory.name+'.command.json'),dict(argv=command,exit_code=child.returncode,
                    environment_overrides={'MOE_UB_HOST_PREPARATION_TRACE':environment['MOE_UB_HOST_PREPARATION_TRACE']},
                    protocol=[first,commit,done,final]))
            finally:
                if child.poll() is None:child.kill()
                child.wait();child.stdout.close()
                if not child.stdin.closed:child.stdin.close()
    assert reports[0]==reports[1]
    save(out/'result.json',dict(passed=True,probe=result))


if __name__=='__main__':main()
