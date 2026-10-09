"""Generate one checked ExecutionPlan and one locked-schema Chakra file per rank."""
import argparse
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess

from .plan import build_plan, validate_plan
from .plan_io import file_sha256, write_plan_json


def write_message(stream, message):
    body = message.SerializeToString(deterministic=True)
    length = len(body)
    while length >= 128:
        stream.write(bytes([(length & 127) | 128]))
        length >>= 7
    stream.write(bytes([length]))
    stream.write(body)


def _schema(output):
    root = Path(__file__).resolve().parents[2]
    schema = root / 'third_party/LLMServingSim/astra-sim/extern/graph_frontend/chakra/schema/protobuf/et_def.proto'
    generated = output / 'schema'
    generated.mkdir()
    command = ['protoc', '-I', str(schema.parent), '--python_out=' + str(generated), str(schema)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    (generated / 'compiler.json').write_text(json.dumps(dict(argv=command, cwd=str(Path.cwd()),
        exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr), indent=2) + '\n')
    if result.returncode:
        raise RuntimeError('Chakra schema generation failed: ' + result.stderr)
    spec = importlib.util.spec_from_file_location('et_def_pb2', generated / 'et_def_pb2.py')
    proto = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proto)
    return proto, schema


def export_bootstrap(output, ranks=range(4)):
    """Metadata-only Chakra graphs: no nodes, business work, or elapsed time."""
    output = Path(output).resolve()
    output.mkdir(exist_ok=False)
    proto, _ = _schema(output)
    for rank in ranks:
        with (output / f'layer.{rank}.et').open('xb') as stream:
            write_message(stream, proto.GlobalMetadata(version='moe-ub-bootstrap/1'))
    return output


def export_plan(plan, output, *, compact=False):
    # A plan supplied by a caller may have changed since construction.
    validate_plan(plan)
    return _write_plan(plan, output, **({'compact': True} if compact else {}))


def build_export_full_model(configs, identities, provenance, model, profile, output, *, source_evidence=None, compact=False):
    """Build, fully validate and write before returning a mutable full plan.

    No receipt/cache or caller-provided 'already validated' flag is accepted.
    The only post-validation additions are the offline source declarations
    previously made by full_round_probe; no executable field is changed.
    Subsequent export of the returned plan must use export_plan and revalidate.
    """
    from .full_model import build_full_model
    plan = build_full_model(configs, identities, provenance, model, profile)
    if source_evidence is not None:
        plan['input_provenance'] = deepcopy(source_evidence)
        for item in [plan, *plan['layers']]:
            item['route_source'] = 'published_model_trace'
            item['compute_source'] = 'full_model_stage_reference_with_context_cost'
    return plan, _write_plan(plan, output, **({'compact': True} if compact else {}))


def _write_plan(plan, output, *, compact=False):
    """Internal writer: called immediately after validation by the two entries."""
    output = Path(output).resolve()
    output.mkdir(exist_ok=False)
    proto, schema = _schema(output)
    transfers = {t['transfer_id']: t for t in plan['transfers']}
    for graph in plan['rank_graphs']:
        with (output / f"layer.{graph['rank_id']}.et").open('xb') as stream:
            write_message(stream, proto.GlobalMetadata(version='moe-ub-execution-plan/1'))
            for task in graph['nodes']:
                node = proto.Node(id=task['node_id'], name=task['name'], data_deps=task['deps'])
                node.attr.add(name='is_cpu_op', bool_val=False)
                if task['kind'] in ('COMP','EVENT'):
                    node.type = proto.COMP_NODE
                    # Locked fork consumes ns despite the legacy field name.
                    node.duration_micros = task.get('duration_ns',0)
                    if task['kind']=='EVENT':
                        # The locked feeder does not initialize absent num_ops.
                        node.attr.add(name='num_ops',int64_val=0)
                else:
                    t = transfers[task['transfer_id']]
                    node.type = proto.COMM_SEND_NODE if task['kind'] == 'SEND' else proto.COMM_RECV_NODE
                    node.attr.add(name='comm_size', int64_val=t['payload_bytes'])
                    for field in ('src', 'dst', 'tag'):
                        node.attr.add(name='comm_' + field,
                            int32_val=t['comm_tag' if field == 'tag' else field + '_rank'])
                write_message(stream, node)
    budget=plan['input'].get('resource_budget')
    if budget is not None:
        (output/'graph-resources.json').write_text(json.dumps(budget)+'\n')
    plan_file=write_plan_json(plan, output/'plan.json', compact=compact,
        max_bytes=budget['max_plan_bytes'] if budget is not None else None)
    files = [schema, output / 'plan.json'] + sorted(output.glob('layer.*.et'))
    if budget is not None:files.append(output/'graph-resources.json')
    (output / 'generation.json').write_text(json.dumps(dict(mode='fixture',
        schema_sha256=file_sha256(schema),
        hashes={p.name: plan_file['sha256'] if p==output/'plan.json' else file_sha256(p) for p in files}), indent=2) + '\n')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        plan = build_plan(json.loads(args.input.read_text()))
    except (ValueError, KeyError, TypeError) as error:
        parser.error(f'{args.input}: {error}')
    path = export_plan(plan, args.output_dir)
    if 'deployment' in plan:
        fabric = [t for t in plan['transfers'] if 'ub_task_id' in t]
        print(f"Generated {len(fabric)} fabric writes / {sum(t['payload_bytes'] for t in fabric)} fabric bytes, "
              f"{len(plan['transfers'])-len(fabric)} local deliveries at {path}")
    else:
        print(f"Generated {len(plan['transfers'])} transfers / "
              f"{sum(t['payload_bytes'] for t in plan['transfers'])} bytes at {path}")


if __name__ == '__main__':
    main()
