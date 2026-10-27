"""Public invalid physical endpoint checks; marker must never start a network."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from .workflow import cli, load, save


def main(repository, stage):
    output = stage/'negative'; output.mkdir()
    marker = output/'backend-started'
    backend = output/'marker-backend'
    backend.write_text('#!/usr/bin/env python3\nfrom pathlib import Path\nPath('+repr(str(marker))+').write_text("started")\nraise SystemExit(97)\n')
    backend.chmod(0o755)
    modern = load(stage/'inputs/modern-input.json')
    modern['deployment'][1]['devices'][0]['nic'] = 2
    modern['deployment'][1]['devices'][1]['nic'] = 1
    streams = load(stage/'inputs/independent-work.json')
    streams['streams'][0]['destination_nic'] = 2
    tx = load(stage/'inputs/transaction-work.json')
    tx['connections'][0]['destination_nic'] = 2
    records = []
    for command, config in (('rdma-stream-probe', streams), ('rdma-transaction-probe', tx), ('modern-deepep-native', modern)):
        source, destination = output/(command+'.json'), output/command
        save(source, config)
        argv = cli(command, '--topology', stage/'independent-topology', '--protocol', stage/'inputs/protocol.json',
                   '--config', source, '--backend', backend, '--output', destination)
        done = subprocess.run(list(map(str, argv)), cwd=repository, env=os.environ,
                              capture_output=True, text=True, timeout=10)
        (output/(command+'.stderr')).write_text(done.stderr)
        (output/(command+'.stdout')).write_text(done.stdout)
        records.append(dict(command=list(map(str, argv)), cwd=str(repository), exit_code=done.returncode,
                            output_created=destination.exists(), marker_started=marker.exists()))
        save(output/'commands.json', records)
        assert done.returncode != 0 and 'no switch path between declared physical NICs' in done.stderr, command
        assert not marker.exists() and not destination.exists(), command
    save(output/'verification.json', dict(passed=True, rejected_before_output_and_backend=3, actual_networks=0, commands=records))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    main(args.repository.resolve(), args.stage.resolve())
