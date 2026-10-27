"""Restore only the public records needed for an offline interaction audit."""
import argparse
import hashlib
import json
from pathlib import Path

from experiments.static_dag_transfer.prepare import admit


SOURCE_SHA = 'e964016c2afa07342cb213f55e2b68c96b12eccb1fe1eff9acac9d1383fa5d99'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    repo, stage = args.repository.resolve(), args.stage.resolve()
    assert not stage.exists()
    blobs = admit(repo/'runs/generic_event_dag_20261027/checkpoint.tar.gz', SOURCE_SHA)
    stage.mkdir()
    selected = {}
    for condition in ('baseline','bandwidth'):
        for name in ('topology.json','protocol.json','initial.json','result.json','modern-plan.json','modern-execution.json'):
            member = f'evidence/reference/{condition}/{name}'
            raw = blobs[member]
            path = stage/condition/name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(raw)
            selected[str(path.relative_to(stage))] = hashlib.sha256(raw).hexdigest()
    sources = ['backend/rdma/fabric.cc']
    base = 'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3/src/'
    sources += [base+'point-to-point/model/'+name for name in ('switch-node.cc','switch-mmu.cc','qbb-net-device.cc')]
    sources += [base+'network/utils/data-rate.cc',base+'core/model/nstime.h']
    hashes = {name:hashlib.sha256((repo/name).read_bytes()).hexdigest() for name in sources}
    (stage/'source.json').write_text(json.dumps(dict(source_archive_sha256=SOURCE_SHA,files=selected,
        source_hashes=hashes,new_networks=0,public_records_only=True),indent=2)+'\n')
    print('Restored',len(selected),'hash-admitted public records; no runtime execution')


if __name__ == '__main__':
    main()
