"""Prove the rejected fresh input never reached native launch; retain attempt."""
import argparse
from pathlib import Path
import shutil
from fixtures.native_wakeup.stage import load, save, sha


def main():
    parser=argparse.ArgumentParser()
    for name in ('repository','stage'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();repo=args.repository.resolve();stage=args.stage.resolve()
    records=load(stage/'commands.json');attempt=next(row for row in records if row['label']=='restored-smoke')
    assert attempt['status']=='terminal' and attempt['exit_code']==1
    assert not (stage/'fresh-smoke').exists() and not (stage/'recovery.json').exists()
    rejected=load(stage/'fresh-input.json')
    assert rejected['modern']['model']['hidden_size']==128
    assert (stage/'fresh-modern.stderr').read_text().strip()=='moe-ub: DeepEP plan: invalid hidden_size'
    assert load(stage/'restored-smoke-monitor/probe.json')['remaining_live_group_pids']==[]
    save(stage/'commands-before-prelaunch-classification.json',records)
    source=repo/'python/moe_ub_cosim/modern_native.py'
    assert 'ModernNativeLayout(t,p,c)  # Pure admission before creating output or native network.' in source.read_text()
    receipt=dict(native_network_started=False,network_attempt_count_retained=True,
        reason='hidden_size=128 rejected by ModernNativeLayout before output creation/worker/native launch',
        failed_input_sha256=sha(stage/'fresh-input.json'),stderr_sha256=sha(stage/'fresh-modern.stderr'),
        public_run_source_sha256=sha(source),original_four_request_input_changed=False)
    save(stage/'fresh-input-admission-rejection.json',receipt)
    attempt.update(network_started=False,classification_proof='fresh-input-admission-rejection.json')
    save(stage/'commands.json',records)
    # Only the fixture driver is corrected in the copied root; platform,
    # executable and eight library bytes remain exactly the admitted snapshot.
    target=stage/'restored/source/fixtures/native_wakeup/recover.py'
    save(stage/'fresh-driver-amendment.json',dict(before_sha256=sha(target),
        after_sha256=sha(repo/'fixtures/native_wakeup/recover.py'),platform_sources_changed=False,
        native_runtime_changed=False,reason='valid independent H512; retain H128 rejection and existing topology'))
    shutil.copyfile(repo/'fixtures/native_wakeup/recover.py',target)


if __name__=='__main__':main()
