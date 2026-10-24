"""Pure final admission/source/interface checks; no network reexecutions."""
import argparse
import ast
from copy import deepcopy
from pathlib import Path
import subprocess
from fixtures.modern_native.stage import load,save,sha
from moe_ub_cosim.modern_native_layout import ModernNativeLayout


def main():
    p=argparse.ArgumentParser()
    for key in ('repository','stage'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve()
    identity=load(s/'source-identity.json');runtime=repo/'python/moe_ub_cosim/modern_native.py'
    assert sha(runtime)==identity['python/moe_ub_cosim/modern_native.py']
    before=(s/'layout-before-serialization-guard.py').read_text();current=(repo/'python/moe_ub_cosim/modern_native_layout.py').read_text()
    expected=before.replace('from .deepep_plan import fields, integer, require','from .deepep_plan import encoded, fields, integer, require').replace(
        "        self.config=deepcopy(config);self.modern=config['modern'];self.plan=compile_plan(self.modern)\n",
        "        self.config=deepcopy(config);self.modern=self.config['modern'];self.plan=compile_plan(self.modern)\n"
        "        require(len(encoded(self.modern))<=self.modern['limits']['max_input_bytes'],'modern serialized input budget exceeded')\n"
        "        require(len(encoded(self.plan))<=self.modern['limits']['max_output_bytes'],'modern serialized plan budget exceeded')\n")
    assert current==expected
    for label in ('synthetic-healthy','synthetic-fault','four-routes','fresh-smoke'):
        root=s/label;layout=ModernNativeLayout(load(root/'topology.json'),load(root/'protocol.json'),load(root/'input.json'))
        assert layout.initial==load(root/'initial.json') and layout.plan==load(root/'modern-plan.json')
    parsed=[]
    for path in [runtime,repo/'python/moe_ub_cosim/modern_native_layout.py',repo/'python/moe_ub_cosim/cli.py',
                 *sorted((repo/'fixtures/modern_native').glob('*.py')),repo/'tests/unit/modern_native_test.py']:
        ast.parse(path.read_text(),filename=str(path));parsed.append(str(path.relative_to(repo)))
    cmd=['python3','-B','-m','moe_ub_cosim','scaleout','--help'];r=subprocess.run(cmd,cwd=repo,capture_output=True,text=True,timeout=5)
    (s/'public-help.stdout').write_text(r.stdout);(s/'public-help.stderr').write_text(r.stderr)
    assert r.returncode==0 and 'modern-deepep-native' in r.stdout
    rejects=[]
    for key in ('max_input_bytes','max_output_bytes'):
        cfg=load(s/'inputs/synthetic.json');cfg['modern']['limits'][key]=1
        try:ModernNativeLayout(load(s/'topology/topology.json'),load(repo/'examples/modern_deepep_native/rdma.json'),cfg)
        except ValueError as e:assert 'serialized' in str(e);rejects.append(key)
        else:raise AssertionError('declared byte budget ignored')
    save(s/'final-checks.json',dict(passed=True,controller_unchanged_after_networks=True,
        layout_delta_only_serialization_admission_and_config_copy=True,all_four_initial_programs_and_plans_equal=True,
        new_declared_serialization_rejects=rejects,additional_network_executions=0,
        final_layout_network_reexecuted=False,parsed_sources=parsed,
        public_help_command=dict(command=cmd,cwd=str(repo),exit_code=r.returncode)))


if __name__=='__main__':main()
