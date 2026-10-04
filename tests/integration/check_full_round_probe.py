"""Check bounded measurement and the real C++ loader on a small complete graph."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-dir',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[2];commands=[]
    binary=a.build_dir.resolve()/'output/cosim/ns3.44-cosim-plan-load-probe'
    def run(label,worker,success=True):
        argv=[sys.executable,'-m','moe_ub_cosim.host_probe','--output-dir',str(out/label),
            '--rss-bytes',str(2**30),'--address-bytes',str(2*2**30),
            '--artifact-bytes',str(2**30),'--wall-seconds','60','--',*map(str,worker)]
        start=datetime.now(timezone.utc).isoformat()
        result=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=75)
        commands.append(dict(argv=argv,cwd=str(root),started_utc=start,exit_code=result.returncode))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        (out/(label+'.stdout.log')).write_text(result.stdout)
        (out/(label+'.stderr.log')).write_text(result.stderr)
        report=json.loads((out/label/'probe.json').read_text())
        assert not report['remaining_live_group_pids'],report
        assert report['status']==('completed' if success else 'child_failed'),report
        assert result.returncode==(0 if success else 1),report
        return report
    run('generate',[sys.executable,'-m','moe_ub_cosim.full_round_probe','small',
        '--output-dir',out/'generate/work'])
    graph=out/'generate/work/graph'
    expected=json.loads((out/'generate/work/result.json').read_text())
    run('load',[binary,graph])
    loaded=json.loads((out/'load/stdout.log').read_text().splitlines()[-1])
    for key in ('nodes','fabric_transfers','local_transfers'):
        assert loaded[key]==expected['actual_usage'][key],(key,loaded,expected)
    assert not loaded['network_initialized'] and not loaded['simulation_executed']
    bad=out/'bad-json';shutil.copytree(graph,bad)
    value=json.loads((bad/'plan.json').read_text());value['rank_graphs'][0]['nodes'][0]['duration_ns']+=1
    (bad/'plan.json').write_text(json.dumps(value)+'\n')
    run('reject-json',[binary,bad],False)
    assert 'composition mismatch' in (out/'reject-json/stderr.log').read_text()
    bad=out/'bad-chakra';shutil.copytree(graph,bad)
    path=next(bad.glob('layer.*.et'));path.write_bytes(path.read_bytes()[:2])
    run('reject-chakra',[binary,bad],False)
    assert 'truncated or malformed graph message' in (out/'reject-chakra/stderr.log').read_text()
    (out/'summary.json').write_text(json.dumps(dict(passed=True,loaded=loaded,
        expected=expected,rejected=['modified_json_node','truncated_chakra'],
        target_scale_executed=False),indent=2)+'\n')
    print('PASS: production full-model build/export/load counts and corrupted JSON/Chakra rejection')


if __name__=='__main__':main()
