"""Hash-admit old evidence and separate prefix inputs from future evaluation."""
import argparse
import hashlib
import json
from pathlib import Path

from experiments.static_dag_transfer.prepare import admit

SOURCE_SHA = 'e964016c2afa07342cb213f55e2b68c96b12eccb1fe1eff9acac9d1383fa5d99'


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    repo, stage = args.repository.resolve(), args.stage.resolve()
    assert not stage.exists()
    blobs = admit(repo/'runs/generic_event_dag_20261027/checkpoint.tar.gz', SOURCE_SHA)
    stage.mkdir()
    hashes = {}
    def copy(member, target):
        raw = blobs[member]
        path = stage/target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        hashes[target] = hashlib.sha256(raw).hexdigest()
    for name in ('event-graph.json', 'program.json'):
        copy('evidence/' + name, 'observed/' + name)
    for condition in ('baseline', 'bandwidth'):
        for name in ('topology.json', 'protocol.json'):
            copy(f'evidence/reference/{condition}/{name}', f'observed/{condition}/{name}')
        copy(f'evidence/{condition}/result.json', f'truth/{condition}/result.json')
        copy(f'evidence/{condition}/control.jsonl', f'truth/{condition}/control.jsonl')
        rows = [json.loads(line) for line in blobs[f'evidence/{condition}/control.jsonl'].splitlines()]
        path = stage/'observed'/condition/'decisions.jsonl'
        path.write_text(''.join(json.dumps(row['message'], separators=(',', ':'))+'\n'
                                for row in rows if row['direction'] == 'native'))
        hashes[str(path.relative_to(stage))] = hashlib.sha256(path.read_bytes()).hexdigest()
    sources = ['backend/rdma/remote_transactions.cc', 'backend/rdma/transaction_session.cc',
               'backend/rdma/persistent_streams.cc', 'backend/rdma/session_callback_batches.hh']
    save(stage/'source.json', dict(archive_sha256=SOURCE_SHA, files=hashes,
        source_hashes={name: hashlib.sha256((repo/name).read_bytes()).hexdigest() for name in sources},
        new_networks=0, future_truth_separated=True))
    print('Restored frozen generic graph and two public sessions; no network execution')


if __name__ == '__main__':
    main()
