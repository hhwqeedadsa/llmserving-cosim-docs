"""Frozen one-factor send-slot comparisons using the existing public workflow."""
from copy import deepcopy
from pathlib import Path
import shutil
import sys

import yaml

from .common import bounded, digest, read, require, save, sources
from .inputs import REFERENCE, SEEDS, make_input, verified_case
from .workflow import verify_complete


def set_slots(value, slots):
    if isinstance(value,dict):
        return {k:(slots if k=='send_slots_per_rank' else set_slots(v,slots)) for k,v in value.items()}
    if isinstance(value,list):
        return [set_slots(v,slots) for v in value]
    return value


def verify_pair_inputs(baseline, treatment):
    baseline,treatment=Path(baseline),Path(treatment)
    names={p.name for p in baseline.iterdir() if p.is_file()}
    require(names=={p.name for p in treatment.iterdir() if p.is_file()}, 'Paired input file set differs')
    for name in names:
        if name=='layers.json':
            old,new=read(baseline/name),read(treatment/name)
            require(all(l['send_slots_per_rank']==1 for g in old['groups'] for l in g['layers']),
                    'Baseline must use one send slot')
            require(set_slots(old,2)==new, 'Layer input differs beyond send slots')
        elif name=='scenario.yaml':
            old,new=[yaml.safe_load((folder/name).read_text()) for folder in (baseline,treatment)]
            require(old['execution']['send_slots_per_rank']==1, 'Baseline scenario must use one send slot')
            require(set_slots(old,2)==new, 'Scenario differs beyond send slots')
        else:
            require(digest(baseline/name)==digest(treatment/name), f'Paired input differs: {name}')


def verify_reference(reference):
    batch=Path(reference['batch']).resolve()
    require(digest(batch/'batch.json')==reference['batch_sha256'], 'Baseline batch changed')
    require(read(batch/'batch.json')['schema_version']=='scaleup-normal-batch/1', 'Need normal baseline batch')
    case,folder=verified_case(batch,reference['case_id'])
    done,summary=verify_complete(batch,case)
    require(done==reference['completion'], 'Baseline completion evidence changed')
    return case,folder,done,summary


def prepare_slots(baseline, output):
    baseline,output=Path(baseline).resolve(),Path(output).resolve()
    require(not output.exists(), 'Slot batch output exists')
    require(read(baseline/'batch.json')['schema_version']=='scaleup-normal-batch/1', 'Need normal baseline batch')
    checked=[]
    for seed in SEEDS:
        case,folder=verified_case(baseline,f's{seed}-b8')
        done,_=verify_complete(baseline,case)
        service,layers,counts=make_input(seed,8)
        require(read(folder/'service.json')==service and read(folder/'layers.json')==layers and
                case['expert_inputs']==counts, 'Baseline differs from fixed input rule')
        for p in REFERENCE.iterdir():
            if p.is_file() and p.name not in ('service.json','layers.json'):
                require(digest(p)==digest(folder/p.name), f'Baseline reference differs: {p.name}')
        checked.append((case,folder,done))
    output.mkdir(parents=True)
    cases=[]
    for base,folder,done in checked:
        name=base['case_id']+'-slots2'
        target=output/'inputs'/name
        shutil.copytree(folder,target)
        save(target/'layers.json',set_slots(read(folder/'layers.json'),2))
        scenario=yaml.safe_load((folder/'scenario.yaml').read_text())
        (target/'scenario.yaml').write_text(yaml.safe_dump(set_slots(scenario,2),sort_keys=False))
        verify_pair_inputs(folder,target)
        case=deepcopy(base)
        case.update(case_id=name,directory=str(target.relative_to(output)),send_slots_per_rank=2,
            files={p.name:digest(p) for p in sorted(target.iterdir()) if p.is_file()},
            baseline=dict(batch=str(baseline),batch_sha256=digest(baseline/'batch.json'),
                          case_id=base['case_id'],completion=done))
        cases.append(case)
    save(output/'batch.json',dict(schema_version='scaleup-slot-batch/1',simulation_executed=False,
        synthetic_input=True,contrast=dict(parameter='send_slots_per_rank',baseline=1,treatment=2,
        input_sets=list(SEEDS),requests_per_rank=8),implementation=sources(),cases=cases))
    return dict(output=str(output),cases=[c['case_id'] for c in cases],reused_baselines=3)


def compare_slots(batch, output):
    batch,output=Path(batch).resolve(),Path(output).resolve()
    require(not output.exists(),'Comparison output exists')
    output.mkdir(parents=True)
    bounded([sys.executable,'-m','experiments.scaleup_observability.slot_analysis',batch,output],
            output/'commands','compare',output)
    return dict(passed=True,output=str(output),pairs=3)
