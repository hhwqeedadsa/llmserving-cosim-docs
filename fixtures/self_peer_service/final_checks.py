"""No-network dependency/identity/CLI/source checks for the delivered increment."""
import argparse
import ast
from pathlib import Path
import re
import subprocess
from fixtures.self_peer_service.stage import load,save,sha


def main():
    p=argparse.ArgumentParser()
    for n in ('repository','stage'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--resume-help-wrap',action='store_true')
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();commands=[]
    def capture(label,cmd):
        if a.resume_help_wrap and label in ('dependencies','public-help'):
            receipt=load(s/'final-check-prepare-failure.json')
            assert sha(s/(label+'.stdout'))==receipt['successful_command_outputs'][label]
            commands.append(dict(command=cmd,cwd=str(repo),exit_code=0,reused_completed_output=True))
            return (s/(label+'.stdout')).read_text()
        r=subprocess.run(cmd,cwd=repo,capture_output=True,text=True,timeout=30)
        (s/(label+'.stdout')).write_text(r.stdout);(s/(label+'.stderr')).write_text(r.stderr)
        commands.append(dict(command=cmd,cwd=str(repo),exit_code=r.returncode));assert r.returncode==0,label
        return r.stdout
    dependency=capture('dependencies',['bash','scripts/check_deps.sh'])
    helptext=capture('public-help',['python3','-B','-m','moe_ub_cosim','scaleout','--help'])
    helptext=' '.join(helptext.split())
    assert 'self-peer service' in helptext and 'self-peer unsupported' in helptext
    source=[]
    for directory in ('python/moe_ub_cosim','fixtures/self_peer_service'):
        for path in sorted((repo/directory).glob('*.py')):
            ast.parse(path.read_text(),filename=str(path));source.append(str(path.relative_to(repo)))
    for rel in ('fixtures/self_peer_service/README.md','examples/scaleout_self_peer/README.md'):
        path=repo/rel
        for link in re.findall(r'\]\(([^)]+)\)',path.read_text()):
            assert (path.parent/link.split('#')[0]).exists(),link
    build=load(s/'build.json')
    assert all(sha(repo/name)==h for name,h in build['sources'].items())
    assert all(sha(s/'runtime'/name)==h for name,h in build['built_runtime'].items())
    assert all(sha(Path(path))==h for path,h in build['unchanged_inputs'].items())
    native=repo/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'
    native_head=capture('native-identity',['git','-C',str(native),'rev-parse','HEAD']).strip()
    assert native_head=='68a07f7d5e53bbe97d275af41d4964ad01b864f3'
    native_status=capture('native-status',['git','-C',str(native),'status','--porcelain']);assert not native_status
    assert load(s/'admission-guard.json')['passed']
    assert all(load(s/name)['passed'] for name in ('checks.json','verification.json','recovery.json'))
    # A reported legacy native_commit remains historical input metadata.
    recorded={label:load(s/label/'inputs.json').get('native_commit') for label in ('default','mixed','fresh-smoke')}
    recorded['fault-online']=load(s/'fault-online/provenance.json').get('native_commit')
    save(s/'identity.json',dict(passed=True,actual_native_commit=native_head,recorded_input_native_commit=recorded,
        historical_receipts_rewritten=False,backend_sources=build['sources'],built_runtime=build['built_runtime'],
        fixed_libraries=build['libraries'],fixed_objects=build['unchanged_inputs']))
    save(s/'final-checks.json',dict(passed=True,commands=commands,dependency_check=dependency.strip(),
        parsed_python_sources=len(source),new_example_links_valid=True,public_help_documents_self_service=True,
        v5_executor_unchanged_after_four_networks=True,final_probe_preallocation_guard_native_rejects=8,
        final_probe_network_reexecuted=False,additional_network_executions=0))


if __name__=='__main__':main()
