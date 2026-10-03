"""Generate one checked ExecutionPlan and one locked-schema Chakra file per rank."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

from .plan import build_plan, validate_plan


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


def export_plan(plan, output):
    validate_plan(plan)
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
                if task['kind'] == 'COMP':
                    node.type = proto.COMP_NODE
                    # Locked fork consumes ns despite the legacy field name.
                    node.duration_micros = task['duration_ns']
                else:
                    t = transfers[task['transfer_id']]
                    node.type = proto.COMM_SEND_NODE if task['kind'] == 'SEND' else proto.COMM_RECV_NODE
                    node.attr.add(name='comm_size', int64_val=t['payload_bytes'])
                    for field in ('src', 'dst', 'tag'):
                        node.attr.add(name='comm_' + field,
                            int32_val=t['comm_tag' if field == 'tag' else field + '_rank'])
                write_message(stream, node)
    (output / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    files = [schema, output / 'plan.json'] + sorted(output.glob('layer.*.et'))
    (output / 'generation.json').write_text(json.dumps(dict(mode='fixture',
        schema_sha256=hashlib.sha256(schema.read_bytes()).hexdigest(),
        hashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}), indent=2) + '\n')
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
    print(f"Generated {len(plan['transfers'])} transfers / "
          f"{sum(t['payload_bytes'] for t in plan['transfers'])} bytes at {path}")


if __name__ == '__main__':
    main()
