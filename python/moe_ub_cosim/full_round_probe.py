"""Offline first-round measurement. Always launch through host_probe limits.

Preparation reads and validates one source file at a time. This is NOT a change
to the service importer, whose full-window resident objects are excluded here.
Build/export use the production closed build/validate/write path. A mutable
plan is returned only after writing; standalone export still revalidates.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import time

from .full_model import model_structure, validate_profile
from .host_preflight import add_token, empty, ref, save, target_inputs, usage
from .plan import RunIdentities, require
from .resources import actual_usage, check_graph, full_usage, validate_budget
from .route_evidence import read_json, validate_raw


class Events:
    def __init__(self, path):
        self.start = time.monotonic()
        self.stream = path.open('x')

    def __call__(self, phase, **values):
        rss = int(Path('/proc/self/statm').read_text().split()[1])
        import os
        row = dict(phase=phase, elapsed_seconds=time.monotonic()-self.start,
                   rss_bytes=rss*os.sysconf('SC_PAGE_SIZE'), **values)
        self.stream.write(json.dumps(row)+'\n'); self.stream.flush()
        print(json.dumps(row), flush=True)

    def close(self):
        self.stream.close()


@contextmanager
def instrument(events):
    """Wrap only coarse API boundaries, with original calls/results unmodified."""
    from . import full_model, multilayer, generate
    build, compose = multilayer.build_plan, full_model.compose_full_model
    write = generate._write_plan
    ordinal = 0

    def layer(config):
        events('layer_begin', layer_id=config['layer_id'])
        result = build(config)
        events('layer_end', layer_id=config['layer_id'], usage=actual_usage(result))
        return result

    def composition(*args, **kwargs):
        nonlocal ordinal
        ordinal += 1
        events('composition_begin', call=ordinal)
        result = compose(*args, **kwargs)
        events('composition_end', call=ordinal, usage=actual_usage(result))
        return result

    def writing(plan, output):
        events('write_begin', usage=actual_usage(plan))
        result = write(plan, output)
        events('write_end')
        return result

    multilayer.build_plan, full_model.compose_full_model = layer, composition
    generate._write_plan = writing
    try:
        yield
    finally:
        multilayer.build_plan, full_model.compose_full_model = build, compose
        generate._write_plan = write


def prepare(config, output, events):
    require(config['schema_version']=='moe-ub-full-round-probe/1', 'Invalid full round probe')
    _, host = read_json(config['host_input'])
    _, counts = read_json(config['counts'])
    _, costs = read_json(config['cost_template'])
    require(counts['input']==host, 'Historical count input differs')
    values, template = target_inputs(host)
    budget = validate_budget(config['graph_budget'])
    template['resource_budget'] = budget
    model = values['capacity']['model_spec']
    cost_template = costs['groups'][0]['full_model_profile']
    profile = dict(schema_version='moe-ub-full-model-cost/1', basis='declared_reference',
                   model_structure=model_structure(model),
                   per_token_ns=deepcopy(cost_template['per_token_ns']), layers=[])
    configs, provenance = [], []
    for block in model['layers']:
        lid = block['layer_id']; moe = block['kind']=='moe'
        cost = deepcopy(cost_template['layers'][1 if moe else 0]); cost['layer_id'] = lid
        profile['layers'].append(cost)
        if moe:
            c = deepcopy(template); c['layer_id'] = lid
            configs.append(c); provenance.append([])
    validate_profile(model, profile)
    ranks = template['rank_ids']; positions = {r:i for i,r in enumerate(ranks)}
    chips = {r['rank_id']:r['chip_id'] for r in template['deployment']['ranks']}
    rows = [empty() for _ in configs]; seen = set(); source_bytes = 0
    manifest = values['manifest']
    events('source_validation_begin')
    for i,(item,q) in enumerate(zip(manifest['files'], values['requests'])):
        require(q==dict(request_id=i+1,rank_id=i%128,arrival_ns=0,file_id=item['file_id'],
                        first_decode_entry=1,decode_steps=64,target_total_tokens=None), 'Target request mapping differs')
        require(item['file']['sha256'] not in seen, 'Duplicate source content')
        seen.add(item['file']['sha256'])
        path, raw = read_json(item['file']); prompt, steps = validate_raw(raw, manifest['layers'])
        require(steps>=64, 'Incomplete source window'); source_bytes += path.stat().st_size
        for c,records,row in zip(configs,provenance,rows):
            lid = c['layer_id']; experts = raw[1][str(lid)][0]
            token = dict(request_id=str(q['request_id']),src_rank=q['rank_id'],phase='decode',
                         token_index=prompt,layer_id=lid,expert_ids=deepcopy(experts))
            c['tokens'].append(token)
            records.append(dict(**{k:v for k,v in token.items() if k!='src_rank'},
                source_file=deepcopy(item['file']),file_id=item['file_id'],source_entry=1,
                source_layer_id=lid,source_token_row=0,decode_index=0,
                source_index_convention='entry_0_prefill_entry_1_first_decode'))
            add_token(row,q['rank_id'],experts,c['expert_placement'],chips,positions)
        del raw
        if (i+1)%128==0: events('source_progress', files=i+1)
    counted = Counter()
    layer_counts = []
    for c,row in zip(configs,rows):
        value = usage(c,row); counted.update(value)
        layer_counts.append(dict(layer_id=c['layer_id'],**value))
    counted['nodes'] += 128*(4+3*8+58*10)
    require(dict(counted)=={k:v for k,v in counts['per_round'][0].items() if k!='step'}, 'First round count mismatch')
    require(source_bytes==counts['source_bytes'], 'Source byte count mismatch')
    expected = check_graph(full_usage(configs,model),budget)
    require(expected=={k:counted[k] for k in expected}, 'Production and independent counts differ')
    bundle = dict(configs=configs,provenance=provenance,model=model,profile=profile,
        source_evidence=dict(manifest=host['manifest'],source=manifest['source'],
            route_model=manifest['model'],target_precision_match=False,infer_eos_from_file_end=False,
            original_observation_steps=64,selected_source_entry=1,
            scope='offline first round only; unchanged production service importer not measured'))
    save(output/'bundle.json',bundle)
    result = dict(schema_version='moe-ub-full-round-prepared/1',input=config,
        bundle=ref(output/'bundle.json'),expected_usage=expected,independent_counts=dict(counted),
        layer_counts=layer_counts,source_files=len(seen),source_bytes=source_bytes,
        selected_records=sum(map(len,provenance)),source_entry=1,original_observation_steps=64,
        compute_basis='positive small-fixture costs repeated over target structure for host allocation only',
        hardware_calibration='unvalidated_reference',target_scale_executed=False)
    save(output/'prepared.json',result)
    events('prepared', usage=expected)


def build_export(bundle, output, events, expected=None):
    from .generate import build_export_full_model
    def record(phase, **values):
        if phase=='write_begin':
            actual=values['usage']
            require(expected is None or actual==expected, 'Actual full round usage differs')
            events('build_end', usage=actual)
            save(output/'built.json',dict(actual_usage=actual,target_scale_executed=False))
            events('export_begin')
        events(phase, **values)
        if phase=='write_end':events('export_end')
    with instrument(record):
        events('build_begin')
        plan, _ = build_export_full_model(bundle['configs'],RunIdentities(),bundle['provenance'],
            bundle['model'],bundle['profile'],output/'graph',source_evidence=bundle.get('source_evidence'))
        actual = actual_usage(plan)
        result = dict(actual_usage=actual,plan_bytes=(output/'graph/plan.json').stat().st_size,
            dependency_edges=sum(len(n['deps']) for g in plan['rank_graphs'] for n in g['nodes']),
            executed_layer_ids=plan['executed_layer_ids'],ranks=len(plan['rank_ids']),
            target_scale_executed=False,simulation_executed=False)
        save(output/'result.json',result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','target','small'))
    parser.add_argument('--input',type=Path)
    parser.add_argument('--output-dir',type=Path,required=True)
    a = parser.parse_args(); a.output_dir.mkdir(parents=True,exist_ok=False)
    events = Events(a.output_dir/'phases.jsonl')
    try:
        if a.action=='prepare':
            require(a.input is not None,'Missing probe config')
            prepare(json.loads(a.input.read_text()),a.output_dir,events)
        elif a.action=='target':
            require(a.input is not None,'Missing prepared manifest')
            prepared = json.loads(a.input.read_text())
            require(prepared['schema_version']=='moe-ub-full-round-prepared/1','Invalid prepared manifest')
            _, bundle = read_json(prepared['bundle'])
            build_export(bundle,a.output_dir,events,prepared['expected_usage'])
        else:
            source = json.loads(Path('fixtures/m10_full_model/service.json').read_text())['groups'][0]
            configs = json.loads(Path('fixtures/m10_full_model/layers.json').read_text())['groups'][0]['layers']
            build_export(dict(configs=configs,provenance=[[] for _ in configs],
                model=source['model_spec'],profile=source['full_model_profile']),a.output_dir,events)
    finally:
        events.close()


if __name__=='__main__':main()
