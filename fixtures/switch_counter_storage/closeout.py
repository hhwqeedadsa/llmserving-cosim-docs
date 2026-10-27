"""Close a bounded platform stage, preserving incomplete acceptance explicitly."""
import argparse
import json
from pathlib import Path
import shutil

from moe_ub_cosim.host_probe import disk_usage


def close(stage,output):
    receipts=[]
    for path in stage.rglob('probe.json'):
        value=json.loads(path.read_bytes())
        assert value['status'] in ('completed','child_failed'),(path,value['status'])
        assert not value['remaining_live_group_pids'],path
        receipts.append((path,value))
    seconds=sum(v['wall_seconds'] for _,v in receipts)
    usage=disk_usage(stage)
    # Includes all copied receipts conservatively; reserve 16MiB for the
    # versioned sources/summary and the closing monitor's final records.
    assert seconds+10<2100 and max(usage.values())+16*2**20<2*2**30
    assert shutil.disk_usage(stage).free>=8*2**30
    verification=json.loads((stage/'verification.json').read_bytes())
    recovery=json.loads((stage/'recovered/recovery.json').read_bytes())
    assert verification['passed'] and recovery['passed']
    value=dict(stage='closed_with_incomplete_native_object_acceptance',overall_research_goal_complete=False,
        functional_result_verification_passed=True,acceptance_complete=False,
        native_object_cases_executed=0,native_object_cases_pending=8,
        network_runs=8,network_reruns=0,full_native_builds_passed=1,standalone_checker_builds_failed=1,
        python_input_methods_passed=4,scaleout_regression_methods_passed=153,
        monitored_seconds_conservative_including_copied_nested_receipts=seconds,
        monitor_receipts_including_copies=len(receipts),stage_artifact_usage=usage,
        final_administration_reserve_bytes=16*2**20,final_administration_reserve_seconds=10,
        wall_budget_seconds=2100,disk_budget_bytes=2*2**30,all_terminal_receipts_report_no_live_owned_children=True,
        offline_recovery=recovery,archive=json.loads((stage/'checkpoint.tar.receipt.json').read_bytes()),
        HTTP=0,GPU=0,push=False)
    output.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();close(a.stage.resolve(),a.output.resolve())
