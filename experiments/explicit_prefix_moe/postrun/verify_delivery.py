"""Generate the original independent proof outside its hashed delivery directory."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--delivery', type=Path, required=True)
    a = p.parse_args()
    stage, delivery = a.stage.resolve(), a.delivery.resolve()
    target = delivery/'delivery_verification.json'
    archived = None
    if target.exists():
        old = json.loads(target.read_bytes())
        assert old['delivery_sha256'].get(target.name) == hashlib.sha256(b'').hexdigest()
        archived = dict(file='delivery_verification-v1.json', sha256=sha(target),
            error='self hash computed while proof file was still empty; rejected by original closeout')
        target.rename(delivery/archived['file'])
    proof = stage/'delivery-proof-v2.json'
    assert not proof.exists()
    with (delivery/'verification_revision.json').open('x') as f:
        json.dump(dict(scope='proof output location only; same independent raw/tamper/curve checks; zero network reruns',
            archived_failure=archived, wrapper_sha256=sha(Path(__file__)),
            original_verifier_sha256=sha(Path(__file__).parents[1]/'delivery_check.py')), f, indent=2)
        f.write('\n')
    subprocess.run([sys.executable, '-B', str(Path(__file__).parents[1]/'delivery_check.py'),
        '--study', str(stage/'study'), '--delivery', str(delivery), '--output', str(proof)], check=True)
    value = json.loads(proof.read_bytes())
    assert value['passed'] and target.name not in value['delivery_sha256']
    for name, h in value['delivery_sha256'].items():
        assert sha(delivery/name) == h
    shutil.copyfile(proof, target)
    print(json.dumps(dict(passed=True, archived_self_hash_failure=archived is not None,
        new_networks=0, proof_sha256=sha(proof))))


if __name__ == '__main__':
    main()
