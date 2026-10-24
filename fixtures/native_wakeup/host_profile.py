"""One pure Python admission measurement on the saved failed-call candidate."""
import argparse
import cProfile
from copy import deepcopy
from pathlib import Path
import pstats
import time
from fixtures.native_wakeup.stage import load, save
from moe_ub_cosim.scaleout_transactions import validate


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',type=Path,required=True)
    args=parser.parse_args();stage=args.stage.resolve();directory=stage/'four-routes'
    program=load(directory/'initial.json')
    program['operations']=load(stage/'partial-controller-reconstructed.json')['controller']['native_operations']
    topology=load(directory/'topology.json');protocol=load(directory/'protocol.json')
    profiler=cProfile.Profile();before=time.perf_counter();profiler.enable()
    validate(topology,protocol,deepcopy(program),allow_no_remote=True)
    profiler.disable();elapsed=time.perf_counter()-before
    profiler.dump_stats(str(stage/'python-admission.prof'))
    stats=pstats.Stats(profiler)
    rows=sorted([dict(file=file,function=name,line=line,primitive_calls=cc,total_calls=nc,
                      own_seconds=tt,cumulative_seconds=ct)
                 for (file,line,name),(cc,nc,tt,ct,_) in stats.stats.items()],
                key=lambda row:row['cumulative_seconds'],reverse=True)[:15]
    failure=load(stage/'failure-analysis.json')
    save(stage/'host-profile.json',dict(network_executed=False,candidate_nodes=len(program['operations']),
        python_validation_seconds_including_profile=elapsed,top_functions=rows,
        last_control_file_write_seconds_after_start=failure['last_control_file_write_seconds_after_start'],
        native_cpu_profile_available=False,network_deadlock_proven=False,
        scope='single offline Python validation; cannot attribute native runtime or whole-run timeout'))


if __name__=='__main__':main()
