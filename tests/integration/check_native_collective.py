"""Compare original and patched ASTRA analytical default collective execution."""
import argparse
import datetime
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

from check_delay import sha
from moe_ub_cosim.generate import write_message


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patched-binary', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    astra = root / 'third_party/LLMServingSim/astra-sim'
    output = args.output_dir.resolve(); output.mkdir(parents=True, exist_ok=False)
    old = astra / 'build/astra_analytical/build/bin/AstraSim_Analytical_Congestion_Unaware'
    new = args.patched_binary.resolve()
    commands = []

    def invoke(label, argv, cwd, stdin=None):
        start = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result = subprocess.run(argv, cwd=cwd, input=stdin, capture_output=True, text=True, check=False)
        (output / (label + '.stdout.log')).write_text(result.stdout)
        (output / (label + '.stderr.log')).write_text(result.stderr)
        commands.append(dict(label=label, argv=argv, cwd=str(cwd), stdin=stdin, started_utc=start,
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), exit_code=result.returncode))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        assert result.returncode == 0, (label, result.stderr)
        return result

    schema = astra / 'extern/graph_frontend/chakra/schema/protobuf/et_def.proto'
    generated = output / 'schema'; generated.mkdir()
    invoke('protoc', ['protoc', '-I', str(schema.parent), '--python_out=' + str(generated), str(schema)], root)
    spec = importlib.util.spec_from_file_location('et_def_pb2', generated / 'et_def_pb2.py')
    proto = importlib.util.module_from_spec(spec); spec.loader.exec_module(proto)
    inputs = output / 'inputs'; inputs.mkdir()
    # Two independent collective nodes exercise the original single resource;
    # both programs receive identical graphs and explicit controller responses.
    for rank in range(8):
        with (inputs / f'collective.{rank}.et').open('xb') as stream:
            write_message(stream, proto.GlobalMetadata(version='0.0.4'))
            for node_id, kind, deps in [(1, 'COMP', []), (2, 'COLL', [1]), (3, 'COLL', [1]), (4, 'COMP', [2, 3])]:
                node = proto.Node(id=node_id, name=f'{kind}-{node_id}', data_deps=deps)
                node.attr.add(name='is_cpu_op', bool_val=False)
                if kind == 'COMP':
                    node.type = proto.COMP_NODE; node.duration_micros = 1000
                else:
                    node.type = proto.COMM_COLL_NODE
                    node.attr.add(name='comm_type', int64_val=proto.ALL_REDUCE)
                    node.attr.add(name='comm_size', int64_val=1048576)
                write_message(stream, node)
    reference = astra / 'tests/rt_template/inputs'
    shutil.copyfile(reference / 'network_cfg.yml', inputs / 'network.yml')
    config = json.loads((reference / 'system_cfg.json').read_text())
    config.update({'roofline-enabled': 0, 'replay-only': 0, 'trace-enabled': 1})
    (inputs / 'system.json').write_text(json.dumps(config, indent=2) + '\n')
    (inputs / 'memory.json').write_text('{}\n')
    evidence = []
    for label, binary in [('baseline', old), ('patched', new)]:
        run = output / label; run.mkdir()
        command = ['timeout', '--kill-after=5s', '30s', str(binary),
            '--workload-configuration=' + str(inputs / 'collective'),
            '--system-configuration=' + str(inputs / 'system.json'),
            '--network-configuration=' + str(inputs / 'network.yml'),
            '--memory-configuration=' + str(inputs / 'memory.json'), '--end-npu-ids=0,1,2,3,4,5,6,7']
        result = invoke(label, command, run, 'done\n' * 7 + 'exit\n')
        assert not result.stderr.strip() and 'All Request Has Been Exited' in result.stdout
        assert 'ERROR:' not in result.stdout and '[critical]' not in result.stdout
        events = re.findall(r'(issue|callback),sys->id=(\d+), tick=(\d+), node->id=(\d+),', (run / 'log/log.log').read_text())
        assert len(events) == 64 and len({(e, r, n) for e, r, _, n in events}) == 64
        timelines = {}
        for event, rank, time, node in events:
            timelines[int(rank), int(node), event] = int(time)
        for rank in range(8):
            assert timelines[rank, 1, 'callback'] == 1000
            assert timelines[rank, 2, 'issue'] == 1000
            assert timelines[rank, 3, 'issue'] == timelines[rank, 2, 'callback']
            assert timelines[rank, 4, 'issue'] == timelines[rank, 3, 'callback']
            assert timelines[rank, 4, 'callback'] == timelines[rank, 4, 'issue'] + 1000
        reports = re.findall(r'sys\[(\d+)\] iteration 0 finished, (\d+) cycles', result.stdout)
        assert len(reports) == 8 and {int(r) for r, _ in reports} == set(range(8))
        assert all(int(t) == timelines[int(r), 4, 'callback'] for r, t in reports)
        # The upstream async logger and direct cout may interleave on stdout.
        # Compare the complete ordered ASTRA log and parsed completion records;
        # retain unmodified stdout/stderr as evidence of the native handshake.
        evidence.append(dict(events=events, reports=reports,
            normalized_astra_log=re.sub(r'^\[\d{4}-\d{2}-\d{2} [^]]+\] ', '',
                                       (run / 'log/log.log').read_text(), flags=re.MULTILINE)))
    assert evidence[0] == evidence[1]
    (output / 'summary.json').write_text(json.dumps(dict(passed=True, ranks=8, collective_nodes=16,
        identical_execution_events=64, completions=evidence[0]['reports'], baseline_sha256=sha(old),
        patched_sha256=sha(new), schema_sha256=sha(schema),
        inputs={p.name: sha(p) for p in inputs.iterdir()}), indent=2) + '\n')
    print('PASS: old/patched native analytical programs, 16 collectives, 64 identical ASTRA events and eight completions')


if __name__ == '__main__':
    main()
