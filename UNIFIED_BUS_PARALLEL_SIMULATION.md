# Unified Bus Parallel Simulation Guide

This guide explains how to run config-driven Unified Bus simulations in four modes:

| Mode | Processes | Threads per process | Use when |
| --- | ---: | ---: | --- |
| Local | 1 | 1 | Establishing a baseline or running a small case |
| Local multi-threaded | 1 | 2 or more | Speeding up one-machine simulations |
| MPI multi-process | 2 or more | Distributing a case across processes or machines |
| MPI plus multi-threaded | 2 or more | Combining process-level and thread-level parallelism |

The recommended entry point for all four modes is `scratch/ub-quick-example`.
The build options and command-line messages use the name MTP for multi-threaded mode.

## Configure And Build

Choose the configuration command that matches the run mode.

Local baseline:

```bash
python3.12 ./ns3 configure --enable-modules=unified-bus --disable-examples --disable-tests --disable-mpi --disable-mtp --disable-werror -d release -G Ninja
```

Local multi-threaded:

```bash
python3.12 ./ns3 configure --enable-modules=unified-bus --disable-examples --disable-tests --disable-mpi --enable-mtp --disable-werror -d release -G Ninja
```

MPI multi-process:

```bash
python3.12 ./ns3 configure --enable-modules=unified-bus --disable-examples --disable-tests --enable-mpi --disable-mtp --disable-werror -d release -G Ninja
```

MPI plus multi-threaded:

```bash
python3.12 ./ns3 configure --enable-modules=unified-bus --disable-examples --disable-tests --enable-mpi --enable-mtp --disable-werror -d release -G Ninja
```

Build the user entry after configuration:

```bash
BUILD_JOBS=${BUILD_JOBS:-$(python3.12 -c 'import os; print(os.cpu_count() or 1)')}
python3.12 ./ns3 build -j "$BUILD_JOBS" scratch/ub-quick-example
```

Use an MPI installation that provides `mpirun` when selecting either MPI mode.

## Run The Four Modes

Local baseline:

```bash
python3.12 ./ns3 run --no-build 'scratch/ub-quick-example --case-path=scratch/2nodes_single-tp'
```

Local multi-threaded:

```bash
python3.12 ./ns3 run --no-build 'scratch/ub-quick-example --case-path=scratch/clos_32hosts-4leafs-8spines_pod2pod --mtp-threads=4'
```

`--mtp-threads` is the number of worker threads in the process. Values `0` and `1` select the local baseline; values of `2` or more enable multi-threaded execution.

MPI multi-process:

```bash
mpirun -np 2 build/scratch/ns3.44-ub-quick-example \
  --case-path=scratch/ub-mpi-minimal \
  --dependency-visibility-delay=20ns
```

MPI plus multi-threaded:

```bash
mpirun -np 2 build/scratch/ns3.44-ub-quick-example \
  --case-path=scratch/ub-mpi-minimal \
  --mtp-threads=2 \
  --dependency-visibility-delay=20ns
```

`-np` is the number of MPI processes. In the combined mode, `--mtp-threads` applies to each process. For example, `-np 2` with `--mtp-threads=4` uses two processes with four worker threads in each process.

The executable name includes the ns-3 version. If the version changes, use the corresponding `ns3.<version>-ub-quick-example` file generated under `build/scratch/`.

## Prepare A Multi-Process Case

The same case directory format is used in every mode. Multi-process runs add the following requirements:

- Give every row in `node.csv` an explicit `systemId`. Its value is the MPI process number that owns the node, starting from `0`.
- Keep `systemId` as a process number in combined MPI plus multi-threaded runs. Do not encode a thread number in this field; thread placement is automatic.
- `topology.csv` may connect nodes owned by different processes. The simulator creates the required local or cross-process connection automatically.
- All processes read the complete `traffic.csv`. A task starts only on the process that owns its `sourceNode`.
- `transport_channel.csv` describes endpoint connectivity. It does not need duplicate rows for every process.

The largest `systemId` must be smaller than the process count passed to `mpirun -np`.

## Configure Task Dependencies

For traffic with phase dependencies, use an explicit positive visibility delay in parallel runs:

```text
--dependency-visibility-delay=<Time>
```

When a task completes, dependent tasks can observe that completion after this delay. The setting makes dependency timing well defined when tasks are owned by different threads or processes.

Use the same value in baseline and parallel runs when comparing their results. A case may derive a positive value from cross-process link delays, but an explicit value is easier to reproduce and audit.

## Compare Baseline And Parallel Results

Parallel execution can choose a different valid order for operations scheduled at exactly the same simulation timestamp. This may change packet timing or task completion timing even when the case and model parameters are identical.

Start with one local baseline and one parallel run using the same case files and parameters. Compare the application metrics that matter to the experiment. For task-level comparison, write canonical task events to separate files:

```bash
python3.12 ./ns3 run --no-build 'scratch/ub-quick-example --case-path=<case-dir> --canonical-output=/tmp/ub_local'
python3.12 ./ns3 run --no-build 'scratch/ub-quick-example --case-path=<case-dir> --mtp-threads=4 --canonical-output=/tmp/ub_mtp4'
```

Define the acceptable difference for the target workload before running a large experiment. A tolerance measured for one case should not be assumed to apply to another case.

## Optional Timing Offsets

When many tasks start at exactly the same simulation time, or when traffic from several paths reaches a node at the same time, small deterministic offsets can reduce sensitivity to simultaneous operations.

These options are disabled by default:

```text
--initial-task-start-offset-window=<Time>
--link-delay-offset-window=<Time>
--timing-offset-seed=<uint32>
```

`--initial-task-start-offset-window` applies only to tasks whose `dependOnPhases` field is empty. Tasks from the same source receive the same non-negative offset in `[0, window)`. Dependent tasks are unchanged.

`--link-delay-offset-window` assigns one fixed non-negative offset in `[0, window)` to each positive-delay physical link. Every packet on that link uses the same adjusted delay. Links configured with `0ns` remain unchanged.

`--timing-offset-seed` makes both assignments reproducible. Use the same two windows and seed in every baseline and parallel run that belongs to one comparison.

Example:

```bash
python3.12 ./ns3 run --no-build 'scratch/ub-quick-example --case-path=scratch/mtp_task_jitter_demo --dependency-visibility-delay=10ns --mtp-threads=4 --initial-task-start-offset-window=32ps --link-delay-offset-window=8ps --timing-offset-seed=1'
```

The `32ps` and `8ps` values are examples, not defaults. Use the smallest windows that meet the experiment's purpose. A larger window reduces the chance that two objects receive the same offset, but it also changes simulated timing more.

These offsets are reproducibility workarounds, not a calibrated hardware delay model. If an experiment relies on them, record both windows and the seed with the results. Use multiple seeds when reporting aggregate behavior, and keep each baseline/parallel pair on the same seed.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `--mtp-threads` reports that MTP is unavailable | Reconfigure with `--enable-mtp`, then rebuild the entry |
| `mpirun` cannot start the program | Verify the MPI installation and configure with `--enable-mpi` |
| A dependent traffic case stops before simulation | Set a positive `--dependency-visibility-delay` |
| A node is assigned to a missing process | Ensure every `systemId` is less than the `-np` value |
| Baseline and parallel outputs differ | Confirm identical case files, dependency delay, offset windows, seed, and model parameters |
| Offset collision warnings appear | Use a larger window only if the experiment can accept the additional timing change |

For a basic local setup, return to [QUICK_START.md](QUICK_START.md). For general Unison examples and integration information, see [UNISON_README.md](UNISON_README.md).
