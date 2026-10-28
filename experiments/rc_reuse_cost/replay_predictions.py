"""Offline replay of frozen decisions, never executes the backend."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


def execute(stage,output):
    assert not output.exists()
    entry=json.loads((stage/'inputs/algorithm.json').read_bytes()); path=stage/entry['path']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256']
    spec=importlib.util.spec_from_file_location('frozen_port_time',path)
    algorithm=importlib.util.module_from_spec(spec); spec.loader.exec_module(algorithm)
    queries=json.loads((stage/'inputs/queries.json').read_bytes()); count=0
    for folder in sorted((stage/'workflows').iterdir()):
        if not (folder/'predictions.json').is_file():continue
        value={q['id']:algorithm.predict(folder/'queries'/q['id']/'observed',q['mode']) for q in queries}
        payload=(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
        assert payload==(folder/'predictions.json').read_bytes(); count+=len(value)
    output.write_text(json.dumps(dict(passed=True,predictions_recomputed=count,network_runs=0),sort_keys=True,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True); a=p.parse_args(); execute(a.stage.resolve(),a.output.resolve())
