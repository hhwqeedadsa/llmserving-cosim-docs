# Run ledger

## Environment
Repo: /home/lry/a100_ep_runtime/第05轮_联合仿真M0_20260919/ns-3-ub/repo
Preexisting dirty: src/unified-bus/model/protocol/ub-transaction.cc and .h; historical scratch cases. Preserved.
Runner: python3.12 ./ns3 run --no-build; no source rebuild/change planned.

## Checkpoint Decisions
2026-10-08 user “继续”: accepted previously proposed experiment. continue_full_matrix with safety stops.

## Case Status
- no_kv_100: success; see execution record below.
- kv_100: success; see execution record below.
- kv_200: success; see execution record below.


### no_kv_100
Status: success; return code: 0; start 2026-10-08T14:29:24.840229+08:00; end 2026-10-08T14:29:27.660371+08:00; wall 2.820 s.
Command: `python3.12 ./ns3 run --no-build 'scratch/ub-quick-example --case-path=scratch/20261008-dependent-moe-kv-contention/cases/no_kv_100 --dependency-visibility-delay=20ns --initial-task-start-offset-window=0ps --canonical-output=/home/lry/a100_ep_runtime/第05轮_联合仿真M0_20260919/ns-3-ub/repo/scratch/20261008-dependent-moe-kv-contention/cases/no_kv_100/canonical'`
Artifacts: {'canonical.rank0.txt': True, 'output/task_statistics.csv': True, 'runlog': True}
Failure: None; retryable: False.
Checkpoint: continue to next pre-registered case.

### kv_100
Status: success; return code: 0; start 2026-10-08T14:29:27.662884+08:00; end 2026-10-08T14:29:31.635998+08:00; wall 3.973 s.
Command: `python3.12 ./ns3 run --no-build 'scratch/ub-quick-example --case-path=scratch/20261008-dependent-moe-kv-contention/cases/kv_100 --dependency-visibility-delay=20ns --initial-task-start-offset-window=0ps --canonical-output=/home/lry/a100_ep_runtime/第05轮_联合仿真M0_20260919/ns-3-ub/repo/scratch/20261008-dependent-moe-kv-contention/cases/kv_100/canonical'`
Artifacts: {'canonical.rank0.txt': True, 'output/task_statistics.csv': True, 'runlog': True}
Failure: None; retryable: False.
Checkpoint: continue to next pre-registered case.

### kv_200
Status: success; return code: 0; start 2026-10-08T14:29:31.638278+08:00; end 2026-10-08T14:29:35.660841+08:00; wall 4.022 s.
Command: `python3.12 ./ns3 run --no-build 'scratch/ub-quick-example --case-path=scratch/20261008-dependent-moe-kv-contention/cases/kv_200 --dependency-visibility-delay=20ns --initial-task-start-offset-window=0ps --canonical-output=/home/lry/a100_ep_runtime/第05轮_联合仿真M0_20260919/ns-3-ub/repo/scratch/20261008-dependent-moe-kv-contention/cases/kv_200/canonical'`
Artifacts: {'canonical.rank0.txt': True, 'output/task_statistics.csv': True, 'runlog': True}
Failure: None; retryable: False.
Checkpoint: continue to next pre-registered case.
