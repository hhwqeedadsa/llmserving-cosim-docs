"""Audit E1/E2 supplement evidence without restoring or rerunning simulations."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tarfile

from .workflow import CONFIG, digest, host, read, save

CASES=('g0-b2-short','g0-b2-expert-half','g0-b2-expert-double','g0-b2-local-fence')
NORMAL=('g0-b2-short','g0-b4-short','g0-b8-short','g0-b8-long')


def require(ok,message):
    if not ok:raise ValueError(message)


def members(archive, inventory, names):
    found={}
    with tarfile.open(archive,'r|gz') as stream:
        for item in stream:
            if item.name not in names:continue
            require(item.isfile() and item.name not in found and item.size<=64*2**20,'Invalid audit member')
            data=stream.extractfile(item).read()
            require(dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())==inventory[item.name],
                    'Audit member does not match archive inventory')
            found[item.name]=json.loads(data)
    require(set(found)==set(names),'Audit members missing')
    return found


def review(root, originals, output):
    root,originals=Path(root).resolve(),Path(originals).resolve()
    backend=read(CONFIG/'batch.json')['backend_sha256'];cases=[]
    original_cache={}
    def original_metadata(name):
        if name not in original_cache:
            archive=originals/(name+'.tar.gz');meta=read(str(archive)+'.json')
            require(meta['verified'] and digest(archive)==meta['archive_sha256'],'Original archive changed')
            original_cache[name]=meta
        return original_cache[name]
    names=('run/manifest.json','run/metrics/validation.json','detail/run/manifest.json',
           'detail/run/metrics/validation.json','detail/selection.json','detail/equivalence.json')
    resource_members=dict(prepare='detail-prepare/probe.json',compare='detail-compare/probe.json',
        replay='detail/run/logs/resources-replay/probe.json',export='detail/run/logs/resources-export/probe.json')
    for name in CASES:
        archive=root/'archives'/(name+'.tar.gz');meta=read(str(archive)+'.json')
        original=original_metadata(name)
        require(meta['verified'] and meta['expanded_released'] and digest(archive)==meta['archive_sha256'],
                'Supplement archive is not verified/released or has changed')
        require(meta['entries']['run/manifest.json']==original['entries']['run/manifest.json'],
                'Original execution manifest changed in supplemental archive')
        data=members(archive,meta['entries'],(*names,*resource_members.values()))
        before,after=data[names[0]],data[names[2]]
        selection,equivalence=data[names[4]],data[names[5]]
        require(before['normal_exit'] and after['normal_exit'] and
                data[names[1]]['passed'] and data[names[3]]['passed'],'Incomplete execution or validation')
        require(before['backend_binary']['sha256']==after['backend_binary']['sha256']==backend==
                selection['original_backend_sha256'],'Backend identity changed')
        require(before['random']==after['random']==equivalence['random'] and
                before['simulation_end_ns']==after['simulation_end_ns']==equivalence['simulation_end_ns'],
                'Execution time or random configuration changed')
        require(equivalence['passed'] and equivalence['packet_wire_and_start_fingerprints_match'] and
                equivalence['ports_compared']==2688,'Missing complete capture noninterference proof')
        for key,member in (('original_manifest_sha256',names[0]),('replay_manifest_sha256',names[2]),('selection_sha256',names[4])):
            require(equivalence[key]==meta['entries'][member]['sha256'],'Equivalence binding mismatch')
        path=root/'paths'/name;summary=read(path/'summary.json')
        require(summary['passed'] and summary['equivalence_sha256']==meta['entries'][names[5]]['sha256'] and
                summary['selection_sha256']==meta['entries'][names[4]]['sha256'],'Path evidence is detached from execution')
        for filename,sha in summary['files'].items():
            require((path/filename).resolve().parent==path and digest(path/filename)==sha,'Path output changed')
        require(summary['complete_selected_application_bytes'] and summary['connected_physical_paths'] and
                summary['actual_data_ready_reconciled'] and summary['queue_integrals_match_aggregates'],
                'Incomplete path/queue evidence')
        resources={phase:(data[member],dict(archive_member=member,sha256=meta['entries'][member]['sha256']))
            for phase,member in resource_members.items()}
        for phase,probe_path in dict(restore=root/'cases'/(name+'-resources')/'probe.json',
                analysis=root/'paths'/(name+'-resources')/'probe.json',
                archive=root/'archives'/(name+'.tar.gz-resources')/'probe.json').items():
            resources[phase]=(read(probe_path),dict(path=str(probe_path.relative_to(root)),sha256=digest(probe_path)))
        resource_summary={}
        for phase,(r,identity) in resources.items():
            expected=host(phase=='replay')
            require(r['status']=='completed' and r['child_exit_code']==0 and not r['remaining_live_group_pids'] and
                    r['limits']==dict(sampled_group_rss_bytes=expected['rss_bytes'],
                        per_process_address_bytes=expected['address_bytes'],artifact_bytes=expected['artifact_bytes'],
                        wall_seconds=expected['wall_seconds']) and
                    r['peak_sampled_artifact_bytes']<=expected['artifact_bytes'] and
                    r['peak_sampled_group_rss_bytes']<=expected['rss_bytes'] and r['wall_seconds']<=expected['wall_seconds'],
                    'Failed phase or changed/exceeded declared resource budget')
            resource_summary[phase]=dict(**identity,**{k:r[k] for k in ('wall_seconds',
                'peak_sampled_group_rss_bytes','peak_sampled_artifact_bytes','limits','status')})
        cases.append(dict(case=name,archive_sha256=meta['archive_sha256'],archive_bytes=meta['archive_bytes'],
            simulation_end_ns=after['simulation_end_ns'],selected_transfers=summary['transfers'],
            selected_packets=summary['packets'],detail_ports=summary['ports'],
            execution_nodes=equivalence['execution_fields']['node_execution']['records'],
            native_transfers=equivalence['execution_fields']['transfers']['records'],
            selection_sha256=meta['entries'][names[4]]['sha256'],paths_sha256=digest(root/'paths'/name/'summary.json'),
            resources=resource_summary))
    duration_files=list((root/'durations').glob('*/summary.json'));durations=[]
    for name in NORMAL:
        original=original_metadata(name)
        matches=[p for p in duration_files if read(p)['archive_sha256']==original['archive_sha256']]
        require(len(matches)==1,'Need exactly one successful duration result per normal case')
        path=matches[0];value=read(path)
        require(value['ports']==2688 and value['complete_declared_coverage'] and
                digest(path.parent/'ports.jsonl')==value['ports_sha256'],'Missing full duration coverage or changed table')
        for member,identity in value['input_members'].items():
            require(original['entries'][member]==identity,'Duration evidence detached from original archive')
        exact_checked=0
        if name==CASES[0]:
            with (path.parent/'ports.jsonl').open() as stream:
                bounds={(r['switch_id'],r['port_id']):r for r in map(json.loads,stream)}
            for r in read(root/'paths'/name/'queues.json'):
                bound=bounds[r['switch_id'],r['port_id']]
                require(all(bound[q+'_nonempty_lower_ns']<=r[q]['nonempty_ns']<=bound[q+'_nonempty_upper_ns']
                            for q in ('voq','fifo')),'Exact replay duration is outside original proven bounds')
                exact_checked+=1
        durations.append(dict(case=name,summary_sha256=digest(path),ports=2688,
            exact_duration_crosschecked_ports=exact_checked,
            stored_windows=value['stored_windows'],logical_windows=value['windows']))
    comparison=read(root/'path-comparison.json')
    require(comparison['baseline_summary_sha256']==cases[0]['paths_sha256'] and
            {c['case'] for c in comparison['cases']}==set(CASES[1:]),'Incomplete cross-condition path comparison')
    for c in comparison['cases']:
        expected=next(r for r in cases if r['case']==c['case'])
        require(c['summary_sha256']==expected['paths_sha256'] and c['tasks_compared']==12 and
                c['packets_compared']==20,'Incorrect fixed-reference comparison coverage')
    save(output,dict(schema_version='issue-screening-delivery-acceptance/1',passed=True,
        scope='E1/E2 supplement, not E3/E4 or hardware calibration',backend_sha256=backend,
        durations=durations,replays=cases,path_comparison_sha256=digest(root/'path-comparison.json'),
        duration_precision='all original ports: proven bounds; selected replay ports: exact queue-state integration',
        remaining_unknown='source/native progression and first-switch pre-departure attribution; no information-gain claim',
        archive_check='archive SHA and selected member bindings; full per-file archive verification already performed at pack',
        program_sha256=digest(__file__)))
    print('E1/E2 supplement bindings and declared coverage passed')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--delivery',type=Path,required=True)
    p.add_argument('--original-archives',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:review(a.delivery,a.original_archives,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],
            str(a.output.resolve())+'-resources',**host(extra_roots=[a.delivery,a.output.parent]))
        if r['status']!='completed':raise SystemExit('Delivery review failed: '+r['status'])


if __name__=='__main__':main()
