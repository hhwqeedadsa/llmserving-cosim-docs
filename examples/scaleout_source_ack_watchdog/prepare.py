"""Prepare independent 128-endpoint Clos source-ACK-deadline inputs."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout-ns', type=int, default=20000)
    args = parser.parse_args()
    output = args.output.resolve()
    from moe_ub_cosim.source_ack_watchdog import configuration
    option = dict(schema_version='moe-source-ack-watchdog/1', timeout_ns=args.timeout_ns,
                  max_messages=128, max_notifications=128)
    configuration(dict(schema_version='moe-scaleout-service/1', transport={},
                       execution_events=dict(schema_version='moe-execution-events/1', max_events=32768),
                       source_ack_watchdog=option))
    subprocess.run([sys.executable, '-B', str(ROOT/'examples/scaleout_contribution_tracking/prepare.py'),
                    '--output', str(output)], cwd=ROOT, check=True)
    config = json.loads((output/'service-tracked.json').read_bytes())
    config['source_ack_watchdog'] = option
    configuration(config)
    with (output/'service-watchdog.json').open('x') as stream:
        json.dump(config, stream, indent=2);stream.write('\n')
    print(json.dumps(dict(passed=True, network_executed=False, source_ack_watchdog=option)))


if __name__ == '__main__':main()
