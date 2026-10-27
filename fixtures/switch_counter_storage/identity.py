"""Save exact built sources, runtime hashes and local incremental dependencies."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')


def identity(repo,stage):
    build=json.loads((stage/'build/backend.json').read_bytes())
    provenance=json.loads((stage/'build/provenance.json').read_bytes())
    for name,digest in provenance['sources'].items():assert sha(repo/name)==digest,name
    dependencies=stage/'dependencies';dependencies.mkdir()
    rows=[]
    for name,path,baseline in [
            ('native','third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3','749794ec6d9925003377d5e1b6cc07f02d5a6417'),
            ('astra','third_party/LLMServingSim/astra-sim','acb6edcd0e6dc800a37d6d6fa4c06f44d807f2b5'),
            ('llm','third_party/LLMServingSim','b62f83510572cefd3fb8fa46591fdaaf89b872a7')]:
        directory=repo/path
        head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=directory,text=True).strip()
        bundle=dependencies/(name+'.bundle')
        subprocess.run(['git','bundle','create',str(bundle),baseline+'..HEAD'],cwd=directory,check=True)
        subprocess.run(['git','bundle','verify',str(bundle)],cwd=directory,check=True,capture_output=True)
        rows.append(dict(name=name,path=path,baseline=baseline,head=head,bundle_sha256=sha(bundle)))
    runtime={};old_runtime={}
    for name in ('small-old','small-default','small-legacy_dense','small-port_dense',
            'failure-legacy_dense','failure-port_dense','large-legacy_dense','large-port_dense'):
        result=json.loads((stage/name/'result.json').read_bytes())
        libraries=[Path(p) for p in result['loaded_libraries'] if Path(p).name.startswith('libns3')]
        assert len(libraries)==8 and all('unified-bus' not in str(p) for p in result['loaded_libraries'])
        hashes={p.name:sha(p) for p in libraries}
        if name=='small-old':old_runtime=hashes
        elif runtime:assert hashes==runtime
        else:runtime=hashes
    before=json.loads((repo/'runs/rc_packet_probe_20261027/build/backend.json').read_bytes())
    result=dict(passed=True,platform_head_built=provenance['platform_head'],native_commit=build['native_commit'],
        backend_sources_match_build=True,compile_units=build['compiler_options']['translation_units'],
        effective_optimization=build['compiler_options']['effective_optimization'],
        dependencies=rows,library_hashes=runtime,old_library_hashes=old_runtime,
        stream_binary_sha256=sha(Path(build['stream_probe'])),old_stream_binary_sha256=sha(Path(before['stream_probe'])),
        same_native_libraries_all_seven_new_runs=True,UB_linked=False,network_runs=8,full_native_builds=1,
        standalone_object_check_compile_attempts=1,standalone_object_check_compile_passed=False,
        HTTP=0,GPU=0,push=False)
    save(stage/'identity.json',result)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('repo','stage'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();identity(a.repo.resolve(),a.stage.resolve())
