"""Explicit one-factor E controls; never patch completed graphs or timestamps."""
from copy import deepcopy
import re


def validate(control, case):
    fields = {'schema_version','case_id','baseline_case','parameter','reference','value'}
    if set(control) != fields or control['schema_version'] != 'issue-screening-control/1':
        raise ValueError('Invalid one-factor control schema')
    if control['baseline_case'] != case['case_id']:
        raise ValueError('Control baseline differs from the selected normal case')
    if not isinstance(control['case_id'],str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]+',control['case_id']):
        raise ValueError('Invalid control case identity')
    if control['case_id'] == control['baseline_case']:
        raise ValueError('Control must have a distinct identity')
    if control['parameter'] == 'routed_expert_per_input_ns':
        if any(type(control[k]) is not int or control[k] <= 0 for k in ('reference','value')):
            raise ValueError('Expert costs must be positive integer nanoseconds')
    elif control['parameter'] == 'copy_fence':
        if control['reference'] != 'data_ready' or control['value'] != 'local_read_complete':
            raise ValueError('Only the declared alternative copy completion condition is supported')
    else:
        raise ValueError('Unsupported control parameter')
    if control['reference'] == control['value']:
        raise ValueError('Control does not change its declared parameter')
    return control


def apply(source, layers, case, control):
    """Copy inputs and change only the selected layers' declared factor."""
    validate(control, case)
    source, layers = deepcopy(source), deepcopy(layers)
    selected = set(range(case['first_layer'],case['first_layer']+case['layer_count']))
    if not selected <= {l['layer_id'] for l in layers}:
        raise ValueError('Control layer coverage is incomplete')
    parameter, reference, value = (control[k] for k in ('parameter','reference','value'))
    if parameter == 'routed_expert_per_input_ns':
        for group in source['service']['groups']:
            profiles = {p['layer_id']:p for p in group['full_model_profile']['layers']}
            for lid in selected:
                if profiles[lid][parameter] != reference:
                    raise ValueError('Expert reference cost differs from the declared baseline')
                profiles[lid][parameter] = value
    for layer in layers:
        if layer['layer_id'] not in selected:
            continue
        target, key = ((layer['compute_profile_ns'],'expert_per_input')
            if parameter == 'routed_expert_per_input_ns' else (layer['cann_profile'],'copy_fence'))
        if target[key] != reference:
            raise ValueError('Layer reference differs from the declared baseline')
        target[key] = value
    return source, layers


def audit(baseline, directory, output):
    """Verify prepared inputs equal a baseline plus its one declared factor."""
    from pathlib import Path
    import yaml
    from .workflow import read, save, digest
    baseline,directory,output=map(Path,(baseline,directory,output))
    if output.exists():raise ValueError('Input audit output must be new')
    base_case,case=read(baseline/'case.json'),read(directory/'case.json')
    control=validate(case['control'],base_case)
    baseline_run=baseline/'run'
    manifest=read(baseline_run/'manifest.json')
    if not (manifest.get('normal_exit') is True and manifest.get('validation') is True):
        raise ValueError('Baseline run must be completed and validated')
    source=read(baseline_run/'inputs/service.json'); layers=read(baseline_run/'inputs/layer.json')
    actual=read(directory/'prepared/service.json'); actual_layers=read(directory/'prepared/layers.json')
    if len(layers['groups'])!=1 or len(actual_layers['groups'])!=1:
        raise ValueError('This batch requires one explicit group')
    expected,changed=apply(dict(service=source),layers['groups'][0]['layers'],base_case,control)
    expected=expected['service'];expected['run_id']=control['case_id']
    layers['groups'][0]['layers']=changed
    if actual!=expected or actual_layers!=layers:
        raise ValueError('Prepared input differs beyond the declared factor and run identity')
    bnet=yaml.safe_load((baseline/'scenario.yaml').read_text())
    cnet=yaml.safe_load((directory/'scenario.yaml').read_text())
    for value in (bnet,cnet):
        for key in ('scenario_id','service','layer'):value.pop(key)
    if bnet!=cnet:raise ValueError('Network or system scenario changed')
    bdir,cdir=baseline_run/'inputs/network',directory/'resolved/network'
    bfiles={p.relative_to(bdir).as_posix():p for p in bdir.rglob('*') if p.is_file()}
    cfiles={p.relative_to(cdir).as_posix():p for p in cdir.rglob('*') if p.is_file()}
    if set(bfiles)!=set(cfiles):raise ValueError('Actual network file coverage changed')
    network={}
    for name,path in bfiles.items():
        if name=='backend.json':
            left,right=read(path),read(cfiles[name])
            left.pop('scenario_id',None);right.pop('scenario_id',None)
            if left!=right:raise ValueError('Effective backend changed beyond scenario identity')
        elif digest(path)!=digest(cfiles[name]):raise ValueError('Actual network file changed: '+name)
        network[name]=dict(baseline_sha256=digest(path),control_sha256=digest(cfiles[name]))
    checks={}
    for name in ('route-manifest.json','prepared/system.json','prepared/observation.json',
                 'prepared/record-policy.json','prepared/export-policy.json'):
        sealed=('run/inputs/'+name.removeprefix('prepared/')) if name in (
            'prepared/system.json','prepared/observation.json','prepared/record-policy.json') else name
        left,right=digest(baseline/sealed),digest(directory/name)
        if left!=right:raise ValueError('Common input changed: '+name)
        checks[name]=left
    if any(base_case[k]!=case[k] for k in ('platform_commit','backend_sha256')):
        raise ValueError('Platform baseline changed')
    br,cr=read(baseline/'run.json'),read(directory/'run.json')
    for key in ('host','export_host'):
        if {k:v for k,v in br[key].items() if k!='artifact_roots'}!={k:v for k,v in cr[key].items() if k!='artifact_roots'}:
            raise ValueError('Host limits changed')
    save(output,dict(schema_version='issue-screening-control-input-audit/1',passed=True,control=control,
        baseline_case_sha256=digest(baseline/'case.json'),control_case_sha256=digest(directory/'case.json'),
        baseline_sealed_service_sha256=digest(baseline_run/'inputs/service.json'),
        baseline_sealed_layers_sha256=digest(baseline_run/'inputs/layer.json'),
        common_input_sha256=checks,network_files=network,
        prepared_source_sha256=digest(directory/'prepared/service.json'),
        prepared_layers_sha256=digest(directory/'prepared/layers.json'),
        equal_except_declared_factor_and_run_identity=True,program_sha256=digest(Path(__file__))))
    print('single-factor input audit passed:',control['case_id'])


def main():
    import argparse
    from pathlib import Path
    p=argparse.ArgumentParser(description='Audit a prepared one-factor control against its normal case')
    p.add_argument('--baseline-case',type=Path,required=True)
    p.add_argument('--case-dir',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();audit(a.baseline_case,a.case_dir,a.output)


if __name__=='__main__':main()
