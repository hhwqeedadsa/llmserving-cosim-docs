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
