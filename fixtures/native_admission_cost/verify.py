"""Rebuild candidates from raw COMMITs, independent of controller/validator."""
import argparse
import json
from pathlib import Path
from fixtures.native_wakeup.stage import load, save, sha


def verify(stage):
    identity=load(stage/'identity.json')
    for name,row in identity['inputs'].items():assert sha(stage/'inputs'/name)==row['sha256']
    program=load(stage/'inputs/initial.json');assert program['operations']==[]
    lines=[json.loads(line) for line in (stage/'inputs/control.jsonl').read_text().splitlines()]
    timings=[json.loads(line) for line in (stage/'timings.jsonl').read_text().splitlines()]
    assert len(lines)==476 and len(timings)==238
    delivered=set();stages=('copy_append','validate_program','validate_wakeups')
    totals={name:dict(wall_ns=0,thread_cpu_ns=0) for name in stages}
    per_candidate=[]
    for index,row in enumerate(timings):
        incoming,outgoing=lines[index*2:index*2+2]
        assert incoming['direction']=='native' and outgoing['direction']=='frontend'
        event,command=incoming['message'],outgoing['message']
        assert event['decision_id']==command['decision_id']==row['decision_id']==index
        assert event['now_ns']==command['now_ns']==row['reported_sim_ns']
        assert command['finish'] is False
        for value in event['events']:
            if 'operation' in value:delivered.add((value['operation'],value['event']))
        program['operations'].extend(command['submissions'])
        assert row['candidate_nodes']==len(program['operations'])
        assert row['submissions']==len(command['submissions'])
        assert row['delivered_transitions']==len(delivered)
        assert row['wake_references']==len(command['wake_on'])
        for name in stages:
            assert set(row[name])=={'wall_ns','thread_cpu_ns'}
            for clock,value in row[name].items():
                assert type(value) is int and value>=0
                totals[name][clock]+=value
        per_candidate.append(sum(row[name]['wall_ns'] for name in stages))
    assert len(program['operations'])==145
    assert load(stage/'final-program.json')==program
    native=json.loads((stage/'measurement-monitor/stdout.log').read_text())
    assert native['passed'] and native['candidates']==238 and native['final_nodes']==145
    assert native['network_executed'] is False
    expected={str(Path(name).resolve()):digest for name,digest in identity['fixed_link_inputs'].items() if name.endswith('.so')}
    assert len(native['libraries'])==len(expected)==8
    assert {str(Path(name).resolve()):sha(Path(name)) for name in native['libraries']}==expected
    monitor=load(stage/'measurement-monitor/probe.json')
    assert monitor['status']=='completed' and monitor['child_exit_code']==0 and not monitor['remaining_live_group_pids']
    elapsed=sum(row['wall_ns'] for row in totals.values())/1e9
    assert elapsed<=monitor['wall_seconds']
    original=identity['original_host_wall_seconds']
    ordered=sorted(per_candidate)
    return dict(passed=True,candidates=238,final_nodes=145,network_executed=False,
        final_program_equals_all_actual_commits=True,eight_loaded_libraries_hash_checked=True,
        timing_unit='nanoseconds',single_pass_totals=totals,measured_total_seconds=elapsed,
        measurement_monitor_seconds=monitor['wall_seconds'],original_host_limit_observation_seconds=original,
        measured_total_divided_by_original_wall=elapsed/original,
        per_candidate_wall_ns=dict(minimum=min(ordered),median_low=ordered[118],maximum=max(ordered)),
        decision_limit_unchanged=256,representative_four_rank_call_complete=False,
        historical_cpu_share_measured=False,
        scope='one offline native admission pass; excludes session progress scans, frontend, I/O and packet events; ratio is not historical attribution')


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True)
    a=p.parse_args();save(a.stage/'verification.json',verify(a.stage.resolve()))


if __name__=='__main__':main()
