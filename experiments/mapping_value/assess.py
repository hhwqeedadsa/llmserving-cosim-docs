"""Reusable preparation/analysis/evaluation orchestration, never a backend driver."""
from pathlib import Path
import sys

from .common import read, require, save
from .inputs import CONDITIONS
from .workflow import command


def assess(run, reference, case, config, conditions, output):
    run,reference,case,config,output = map(lambda p:Path(p).resolve(),(run,reference,case,config,output))
    require(not output.exists(), 'Assessment output already exists')
    require(conditions and len(set(conditions))==len(conditions) and set(conditions)<=CONDITIONS, 'Invalid conditions')
    output.mkdir(parents=True); logs=output/'commands'; prefix=[sys.executable,'-m','experiments.mapping_value']
    command([*prefix,'prepare','--run',run,'--reference',reference,'--config',config,'--output',output/'prepared'],logs,'prepare')
    results={}
    for condition in conditions:
        analysis=output/'analysis'/(condition+'.json'); evaluation=output/'evaluation'/(condition+'.json')
        command([*prefix,'analyze','--input',output/'prepared'/condition,'--output',analysis],logs,condition+'-analyze')
        command([*prefix,'evaluate','--run',run,'--reference',reference,'--analysis',analysis,
            '--case',case,'--config',config,'--prepared',output/'prepared','--output',evaluation],logs,condition+'-evaluate')
        report=read(evaluation)
        results[condition]={k:report[k] for k in ('round_delta_ns','material_round_effect','material_prediction_correct',
            'assessment','endpoint_candidates','port_candidate_count','changed_port_count','selected_call_hit',
            'port_target_in_candidates','selected_call_target_wrongly_excluded','application_window_hit')}
    save(output/'summary.json',dict(schema_version='mapping-value-assessment/1',results=results))
    return dict(passed=True,output=str(output),results=results)
