"""Verify extraction of the public auditor against retained full executions."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--retained',type=Path,required=True)
    p.add_argument('--failed',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    repo=Path(__file__).resolve().parents[2];retained=a.retained.resolve();commands=[]
    old=subprocess.check_output(['git','show','c35721160c:tests/integration/audit_legacy_native.py'],cwd=repo,text=True)
    new=(repo/'python/moe_ub_cosim/legacy_audit.py').read_text()
    def functions(source):return {n.name:n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef)}
    previous,current=functions(old),functions(new)
    for name in ('decode','verify_complete_ids','verify_packet_bytes','audit'):
        node=current[name]
        for item in ast.walk(node):
            if isinstance(item,ast.keyword) and item.arg=='route_source_verified':item.arg='real_routes'
        assert ast.dump(previous[name])==ast.dump(node),name
    def command(name,args,success=True):
        result=subprocess.run([sys.executable,'-B',*map(str,args)],cwd=repo,capture_output=True,text=True,timeout=100)
        (out/(name+'-stdout.log')).write_text(result.stdout);(out/(name+'-stderr.log')).write_text(result.stderr)
        commands.append(dict(name=name,arguments=list(map(str,args)),exit_code=result.returncode))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        assert (result.returncode==0)==success,(name,result.stderr)
        return result
    command('packet-unit',['tests/unit/legacy_packet_audit_test.py'])
    help_result=command('help',['-m','moe_ub_cosim','scaleout','deepep-audit','--help'])
    assert '--run' in help_result.stdout
    sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    retained_hashes={}
    for case in ('normal','fault','cross-call'):
        run=retained/case;protocol=retained/'inputs'/('fault.json' if case=='fault' else 'normal.json')
        output=out/(case+'.json');before=sha(run/'result.json')
        command(case,['-m','moe_ub_cosim','scaleout','deepep-audit','--run',run,'--protocol',protocol,'--output',output])
        summary=json.loads(output.read_bytes());expected=json.loads((retained/(case+'-audit.json')).read_bytes())
        assert summary.pop('schema_version')=='moe-legacy-native-audit/1'
        assert 'route' in summary.pop('scope')
        for name,digest in summary.pop('input_sha256').items():assert sha(Path(name))==digest
        summary['real_routes']=summary.pop('route_source_verified')
        assert summary==expected and before==sha(run/'result.json')
        retained_hashes[case]=before
    output=out/'normal.json';before=sha(output)
    command('no-overwrite',['-m','moe_ub_cosim','scaleout','deepep-audit','--run',retained/'normal',
        '--protocol',retained/'inputs/normal.json','--output',output],success=False)
    assert sha(output)==before
    rejected=out/'optimized.json'
    optimized=command('optimized',['-O','-m','moe_ub_cosim','scaleout','deepep-audit','--run',retained/'normal',
        '--protocol',retained/'inputs/normal.json','--output',rejected],success=False)
    assert 'requires Python assertions' in optimized.stderr and not rejected.exists()
    rejected=out/'failed.json'
    command('incomplete',['-m','moe_ub_cosim','scaleout','deepep-audit','--run',a.failed,
        '--protocol',retained/'inputs/normal.json','--output',rejected],success=False)
    assert not rejected.exists()
    evidence=dict(passed=True,unchanged_check_function_bodies=4,retained_full_runs_read=3,
        original_results_unchanged=True,retained_result_sha256=retained_hashes,new_network_executions=0,
        optimized_python_rejected=True,existing_output_preserved=True,incomplete_run_rejected=True,
        route_source_verified_by_public_audit=False,
        public_source_sha256=sha(repo/'python/moe_ub_cosim/legacy_audit.py'),
        wrapper_source_sha256=sha(repo/'tests/integration/audit_legacy_native.py'),source_sha256=sha(Path(__file__)))
    (out/'verification.json').write_text(json.dumps(evidence,indent=2)+'\n');print(json.dumps(evidence))


if __name__=='__main__':main()
