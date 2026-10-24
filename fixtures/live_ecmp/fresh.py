"""New Python source root/empty cwd, sixth and final actual network."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def load(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, x): p.write_text(json.dumps(x, indent=2)+'\n')


def main():
    p = argparse.ArgumentParser(); p.add_argument('--stage', type=Path, required=True); a = p.parse_args()
    s = a.stage.resolve(); restored = s/'restored'; source = restored/'source'; ev = restored/'evidence'; out = s/'fresh'
    assert load(s/'run.json')['actual_networks'] == 5 and not out.exists()
    build = load(ev/'build.json'); backend = s/'bin/stream-probe'
    assert sha(backend) == build['binaries']['stream-probe'] and all(sha(Path(p)) == h for p, h in build['libraries'].items())
    manifest = load(restored/'manifest.json')
    for name, entry in manifest['files'].items(): assert sha(restored/name) == entry['sha256']
    out.mkdir(); cwd = out/'empty-cwd'; cwd.mkdir()
    env = dict(os.environ, PYTHONPATH=str(source/'python')+os.pathsep+str(source), PYTHONDONTWRITEBYTECODE='1')
    commands = []
    def run(label, argv):
        argv = list(map(str, argv))
        with (out/(label+'.stdout.log')).open('w') as stdout, (out/(label+'.stderr.log')).open('w') as stderr:
            r = subprocess.run(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr, timeout=60)
        commands.append(dict(label=label, command=argv, cwd=str(cwd), pythonpath=env['PYTHONPATH'], exit_code=r.returncode)); save(out/'commands.json', commands)
        assert r.returncode == 0, label+': no retry'
    cli = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout']; example = source/'examples/scaleout_live_ecmp'
    run('topology', [*cli, 'topology', '--config', example/'topology.json', '--output', out/'topology'])
    run('network', [*cli, 'rdma-stream-probe', '--topology', out/'topology', '--protocol', example/'protocol.json',
        '--config', example/'work-empty-route.json', '--backend', backend, '--output', out/'network'])
    # Use the snapshotted independent route and transport oracle from source.
    code = "from pathlib import Path; import json; from fixtures.live_ecmp.audit import audit; load=lambda p:json.loads(Path(p).read_text()); import sys; r=audit(load(sys.argv[1]),load(sys.argv[2]),load(sys.argv[3]),load(sys.argv[4])); assert r['local_drops'].get('no_live_old_candidate',0)>0; Path(sys.argv[5]).write_text(json.dumps(r,indent=2)+chr(10))"
    run('fresh-audit', [sys.executable, '-B', '-c', code, out/'topology/topology.json', example/'protocol.json', example/'work-empty-route.json', out/'network/result.json', out/'audit.json'])
    run('primary-offline', [sys.executable, '-B', '-m', 'fixtures.live_ecmp.audit', '--stage', ev, '--output', out/'primary-audit.json'])
    assert (out/'primary-audit.json').read_bytes() == (ev/'audit.json').read_bytes()
    assert sha(backend) == build['binaries']['stream-probe']
    save(out/'replay.json', dict(passed=True, actual_networks_total=6, new_python_source_root=True, empty_cwd=True,
        prior_five_offline_audits_byte_identical=True, empty_old_candidate_has_actual_packet_drop=True,
        fresh_case=load(out/'audit.json'), native_runtime_bundled=False, commands=commands,
        scope='new Python source/public small workflow with fixed external native binary/libraries; not clean build or research performance'))


if __name__ == '__main__': main()
