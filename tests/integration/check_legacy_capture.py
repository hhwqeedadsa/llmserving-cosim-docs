"""Read and replay an actual saved v0.3 small bundle with the current tools."""
import argparse
from datetime import datetime,timezone
from itertools import zip_longest
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.frozen_replay import network_input
from moe_ub_cosim.record_storage import iter_result,iter_port_records
from moe_ub_cosim.user_io import inventory


def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n');return p
def equal(a,b,label):
    absent=object();count=0
    for x,y in zip_longest(a,b,fillvalue=absent):assert x==y,(label,count,x,y);count+=1
    return count


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--slice',type=Path,required=True)
    p.add_argument('--legacy-structured-network',type=Path,required=True);p.add_argument('--backend',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
    root=Path(__file__).resolve().parents[2];out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    source=a.slice.resolve();binary=a.backend.resolve();example=root/'examples/first_round';commands=[]
    def cli(label,*args):
        argv=list(map(str,[sys.executable,'-m','moe_ub_cosim',*args]));r=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=360)
        for stream in ('stdout','stderr'):(out/(label+'.'+stream+'.log')).write_text(getattr(r,stream))
        commands.append(dict(argv=argv,cwd=str(root),finished_utc=datetime.now(timezone.utc).isoformat(),exit_code=r.returncode));save(out/'commands.json',commands)
        assert r.returncode==0,(label,r.stderr);return json.loads(r.stdout)
    info=cli('inspect','slice','inspect','--slice',source,'--verify')
    assert info['capture_capabilities']['storage']=='detailed' and not info['capture_capabilities']['queue_statistics']
    sample=read(example/'sample.json');sample.update(export_policy=str(example/'export-policy.json'))
    cli('sample','sample','--slice',source,'--config',save(out/'sample.json',sample),'--output',out/'sample')
    replay=read(example/'replay.json');replay.update(backend=str(binary),export_policy=str(example/'export-policy.json'))
    cli('replay','replay','--slice',source,'--config',save(out/'replay.json',replay),'--output',out/'replay')
    cli('validate','validate','--run',out/'replay')
    old=read(source/'raw/execution/result.json');new=read(out/'replay/raw/execution/result.json')
    assert old['normal_exit'] and new['normal_exit'] and old['simulation_end_ns']==new['simulation_end_ns']
    compared={name:equal(iter_result(source/'raw/execution',name,old),iter_result(out/'replay/raw/execution',name,new),name)
              for name in old['record_storage']['fields']}
    ports=0
    for port in sorted((source/'raw/execution/ports').iterdir()):
        current=out/'replay/raw/execution/ports'/port.name
        assert read(port/'observation-audit.json')==read(current/'observation-audit.json')
        for name in ('packet-events.jsonl','truth/packet_ranges.jsonl'):
            equal(iter_port_records(source/'raw/execution',port,name),iter_port_records(out/'replay/raw/execution',current,name),(port.name,name))
        ports+=1
    # Large historical topology is only parsed/validated here, never executed.
    before=inventory(a.legacy_structured_network)
    rebound=network_input(a.legacy_structured_network,out/'legacy-network-current',None,binary)
    assert inventory(a.legacy_structured_network)==before
    migration=rebound['catalog_revision_rebinding']
    assert migration['compatibility_rule']=='v03_write_inactive_native_parameters/1'
    save(out/'summary.json',dict(passed=True,historical_slice=str(source),simulation_end_ns=old['simulation_end_ns'],
        actual_legacy_execution_equal=compared,actual_legacy_port_event_streams_equal=ports,legacy_sampling_succeeded=True,
        large_network_catalog_check_only=migration,large_network_simulation_executed=False))
    print('PASS actual historical detailed records, passive resampling, exact small replay and narrow inactive-native catalog migration')


if __name__=='__main__':main()
