"""Stream two completed executions and compare every logical record and port table."""
import argparse
from itertools import zip_longest
from pathlib import Path

from moe_ub_cosim.record_storage import iter_result, iter_port_records
from moe_ub_cosim.user_io import read, save


def equal(left, right, label):
    missing = object()
    count = 0
    for before, after in zip_longest(left, right, fillvalue=missing):
        assert before == after, (label, count)
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected', type=Path, required=True)
    parser.add_argument('--actual', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    before, after = (read(p/'result.json') for p in (args.expected, args.actual))
    assert before['normal_exit'] and after['normal_exit']
    assert before['network_drain']['idle'] and after['network_drain']['idle']
    # Compressed file partitioning and host paths are not simulated behavior.
    for key in before.keys() | after.keys():
        if key not in ('record_storage', 'loaded_libraries'):
            assert before[key] == after[key], key
    assert set(before['record_storage']['fields']) == set(after['record_storage']['fields'])
    fields = {name: equal(iter_result(args.expected, name, before),
                          iter_result(args.actual, name, after), name)
              for name in before['record_storage']['fields']}
    ports = {}
    assert {p.name for p in (args.expected/'ports').iterdir()} == {p.name for p in (args.actual/'ports').iterdir()}
    for port in sorted((args.expected/'ports').iterdir()):
        other = args.actual/'ports'/port.name
        assert read(port/'observation-audit.json') == read(other/'observation-audit.json'), port.name
        names = read(port/'record-storage.json')['fields']
        assert set(names) == set(read(other/'record-storage.json')['fields'])
        ports[port.name] = {name: equal(iter_port_records(args.expected, port, name),
                                       iter_port_records(args.actual, other, name), (port.name, name))
                           for name in names}
    save(args.output, dict(passed=True, simulation_end_ns=before['simulation_end_ns'],
                           fields=fields, ports=ports, network_drain=before['network_drain']))
    print('PASS identical execution, native callbacks, dependencies and all captured port records')


if __name__ == '__main__':
    main()
