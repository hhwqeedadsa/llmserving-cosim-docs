"""Freeze actual commands and same-O0 link inputs; never build a dependency."""
import argparse
import shutil
from pathlib import Path
from fixtures.native_wakeup.stage import load, save, sha


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage','source-stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve();old=a.source_stage.resolve()
    failure=load(old/'failure-analysis.json');assert failure['actual_control_exchanges']==238
    assert not failure['four_request_full_call_passed'] and not (old/'four-routes/result.json').exists()
    inputs=stage/'inputs';inputs.mkdir()
    files={}
    for name in ('initial.json','topology.json','protocol.json','control.jsonl'):
        original=old/'four-routes'/name;target=inputs/name
        shutil.copyfile(original,target);files[name]=dict(source=str(original),sha256=sha(target))
    identity=load(old/'runtime-identity.json')
    fixed={**identity['unchanged_objects'],**identity['libraries']}
    assert all(sha(Path(name))==digest for name,digest in fixed.items())
    commands=load(old/'build-commands.json')
    compile=commands['compile'].copy()
    for flag in ('-MD','-c'):compile.remove(flag)
    for flag in ('-o','-MT','-MF'):
        index=compile.index(flag);del compile[index:index+2]
    compile[-1]=str(repo/'fixtures/native_admission_cost/measure.cc')
    compile.extend([*identity['unchanged_objects'],'-Wl,--no-as-needed',*sorted(identity['libraries']),'-o',str(stage/'measure')])
    libraries=old/'runtime/lib'
    measure=['env','LD_LIBRARY_PATH='+str(libraries),str(stage/'measure'),
        str(inputs/'initial.json'),str(inputs/'topology.json'),str(inputs/'control.jsonl'),
        str(load(inputs/'protocol.json')['limits']['max_sim_ns']),str(stage/'timings.jsonl'),str(stage/'final-program.json')]
    headers={str(path.relative_to(repo)):sha(path) for path in (repo/'backend/rdma').glob('*.hh')}
    save(stage/'identity.json',dict(platform_commit='fdb77c2',inputs=files,fixed_link_inputs=fixed,
        native_headers=headers,driver_sha256=sha(repo/'fixtures/native_admission_cost/measure.cc'),
        compile_command=compile,measurement_command=measure,
        original_host_wall_seconds=load(old/'four-routes-monitor/probe.json')['wall_seconds'],
        network_executed=False,dependencies_rebuilt=False,optimization='same last -O0 as actual session',
        scope='same-host native admission diagnostic; original host CPU division unknown'))


if __name__=='__main__':main()
