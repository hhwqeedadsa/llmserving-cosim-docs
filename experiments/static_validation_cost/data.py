"""Independent bounded archive and immutable input helpers for this study."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import tarfile

OLD_SHA = '4d7360e1c18b6847ebeb0fa670aa705232c15bce2b9d0df0279ac4ea127473b9'
IDENTITY_SHA = 'e0a6015eb54f6cd9f5ca77cc9c2ff22c9fe1389d52e58fc55ca5b58627d43ac6'
CASES = [(4, 'modern'), (4, 'generic'), (8, 'modern'), (8, 'generic')]


def load(path): return json.loads(path.read_bytes())
def save(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def admit(path, digest, schema):
    assert path.stat().st_size < 128*2**20 and sha(path) == digest
    files = {}; total = 0
    with tarfile.open(path) as source:
        for member in source:
            name = PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts
            assert str(name) == member.name and '\\' not in member.name and member.name not in files
            assert member.size < 64*2**20 and len(files) < 4096
            total += member.size; assert total < 512*2**20
            raw = source.extractfile(member).read(); assert len(raw) == member.size
            files[member.name] = raw, stat.S_IMODE(member.mode)
    raw, _ = files.pop('manifest.json'); manifest = json.loads(raw)
    assert manifest['schema_version'] == schema and set(manifest['files']) == set(files)
    for name, (raw, mode) in files.items():
        entry = manifest['files'][name]
        assert entry == dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), mode=mode)
        assert mode in (0o644, 0o755) and not any(str(p) in files for p in PurePosixPath(name).parents)
    return files


def schedule():
    rows = []
    for index, (batch, mode) in enumerate(CASES):
        for pair in range(3):
            order = (False, True) if (index+pair)%2 == 0 else (True, False)
            for cached in order:
                rows.append(dict(label=f'b{batch}-{mode}-p{pair}-'+('cached' if cached else 'full'),
                    batch=batch, mode=mode, pair=pair, cached=cached, timing=False))
    for batch, mode in CASES:
        rows.append(dict(label=f'b{batch}-{mode}-diagnostic', batch=batch, mode=mode,
                         pair=None, cached=True, timing=True))
    return rows


def projected(result):
    value = deepcopy(result)
    libraries = value['loaded_libraries']
    native = [Path(n).name for n in libraries if Path(n).name.startswith('libns3.')]
    assert len(native) == 8 and len(set(native)) == 8
    value['loaded_libraries'] = dict(native=sorted(native),
        system=[n for n in libraries if not Path(n).name.startswith('libns3.')])
    return value


def runtime_check(stage, source=None):
    assert sha(stage/'runtime-identity.json') == IDENTITY_SHA
    identity = load(stage/'runtime-identity.json')
    assert len(identity['files']) == 10
    assert all(sha(stage/'runtime'/n) == h for n, h in identity['files'].items())
    if source is not None: assert all(sha(source/n) == h for n, h in identity['sources'].items())


def behavior(stage, row, *, live=False):
    directory = stage/'truth'/row['label']
    reference = stage/'reference'/f"batch{row['batch']}"/row['mode']
    actual, old = load(directory/'result.json'), load(reference/'result.json')
    assert projected(actual) == projected(old), 'complete native behavior changed'
    assert actual['passed'] and actual['session']['finished'] and actual['drained']
    assert actual['packet_capture']['complete'] and actual['packet_capture']['omitted_events'] == 0
    snapshot = 'modern-execution.json' if row['mode'] == 'modern' else 'event-execution.json'
    assert load(directory/snapshot) == load(reference/snapshot), 'application changed'
    assert (directory/'control.jsonl').read_bytes() == (reference/'control.jsonl').read_bytes(), 'control changed'
    count = actual['session']['decisions']
    if row['cached']:
        assert load(directory/'static-validation.json') == dict(schema_version='moe-static-stream-validation/1',
            host_only=True, calls=count+1, hits=count, validations=1, unkeyable=0, entries=1)
    else: assert not (directory/'static-validation.json').exists()
    if live:
        identity = load(stage/'runtime-identity.json')
        native = [Path(n) for n in actual['loaded_libraries'] if Path(n).name.startswith('libns3.')]
        assert all(p.parent == stage/'runtime/lib' and sha(p) == identity['files']['lib/'+p.name] for p in native)
    return dict(passed=True, decisions=count, source_packets=len(actual['transactions']['transport']['source_packets']),
                physical_TX=sum(e[3] == 0 for e in actual['packet_capture']['events']),
                callbacks=len(actual['session']['events']), finish_ns=actual['finish_ns'])
