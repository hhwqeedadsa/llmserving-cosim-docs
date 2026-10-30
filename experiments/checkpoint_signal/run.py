"""Run only checkpoint classifiers, passing plan and past observations."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

def load(path):
    return json.loads(path.read_bytes())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    frozen = load(args.prepared/'frozen.json')
    for name, digest in frozen['prepared_sha256'].items():
        assert sha(args.prepared/name) == digest
    classifier = Path(__file__).with_name('classify.py').resolve()
    assert sha(classifier) == frozen['classifier_sha256']
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    commands = []
    for name, plan in frozen['plans'].items():
        for condition in ('normal', 'local'):
            for view in ('all_members', 'local_member', 'final_only'):
                event_file = args.prepared/'observed'/f'{name}-{condition}-{"minimal" if view == "local_member" else "full"}.jsonl'
                output = out/f'{name}-{condition}-{view}.json'
                command = [sys.executable, '-B', str(classifier), '--plan', plan,
                           '--events', str(event_file.resolve()), '--view', view, '--output', str(output)]
                result = subprocess.run(command, text=True, capture_output=True)
                commands.append(dict(command=command, cwd=str(Path.cwd()), exit_code=result.returncode,
                                     stdout=result.stdout, stderr=result.stderr))
                (out/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
                assert result.returncode == 0, result.stderr
    print(json.dumps(dict(passed=True, classifier_processes=len(commands), new_network_executions=0)))

if __name__ == '__main__':
    main()
