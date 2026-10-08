# Dependency-driven multi-group MoE/KV experiment

## Planning Mode and Approval
experiment-group. User approved the proposed 8-host / 3 inference groups / KV / 100 vs 200 Gbps / approximately 8000 us design with “继续” on 2026-10-08 (Asia/Shanghai).
Implementation details below make that bounded design reproducible.

## Claim
How do overlapping completion-driven MoE stages and bursty background KV traffic reshape long-window bandwidth, queue occupancy and inference communication latency?

## Simulator Boundary
Not a new A100 measurement, not production traffic, not online LLMServingSim/ns-3 co-simulation.
Replay the zero-based Block 24 template ten times for each of three independent two-rank replicas.
Do not call those repetitions ten real model blocks, requests or decode tokens.
Compute durations and logical collective bytes come from the prior LLMServingSim Block24 evidence.
Profile hardware is RTX PRO 6000 Blackwell Server Edition, not A100.
Source absolute communication timestamps are reconstructed and are NOT reused.
AR = two 2-rank ring exchanges of 262144 B/direction; dispatch AG = 278528 B/direction; combine RS = 262144 B/direction.
Mapping to URMA_WRITE is an algorithm/transport projection, not NCCL/HCCL channel tracing.
ns-3 supplies new packet, task and queue timing. Dependency release uses task completion +20 ns, not final ACK.
The 36.330 / 2.549 / 456.737 us delays model dense compute / LN / expert compute.
No GPU kernel executor, token router, scheduler or real compute/network contention is modeled.

## Topology and Workload
Custom graph: hosts 0..3 -> switch8; hosts4..7 -> switch9; core8<->9.
Access 400 Gbps/20 ns; core 100 or 200 Gbps/50 ns, full duplex.
Input processing 10 ns; allocation 1 ns. HASH shortest deterministic routes; no multipath/spray.
Groups G0=(0,4), G1=(1,5), G2=(2,6); group starts 0/40/80 us.
For each of 10 repetitions: compute36.330 -> AR-RS -> AR-AG -> LN2.549 -> dispatch-AG -> expert456.737 -> combine-RS; next repetition depends on combine-RS.
Both directions of a phase must complete before dependents are released. Phase IDs are group-local uniquely allocated.
Background KV proxy host3->7: 524288 B each; release = 800*k + [100,140,180,500,520] us, k=0..9.
KV releases and group arrival offsets are synthetic. KV size is a proxy, not a real KV handoff trace.
Priority7, CBFC, CC off, retransmission off, on-demand RTP. No hardware calibration.
0..8000 us plotting window; simulation drains all tasks (no early stop); validate final data/ACK before horizon.

## Control And Treatments
Block KV: no_kv_100 -> kv_100 changes KV presence only.
Block BW: kv_100 -> kv_200 changes core bandwidth only.
Fixed controls: all inference tasks, dependencies, source sizes/delays, group mapping, access bandwidth/latency, core propagation, flow control, routing, transport, trace settings and metric definitions.
Full resolved catalog snapshots; input differences verified before analysis.

## Prediction And Falsification
All cases: all tasks and phases complete; payload conserves; no port exceeds capacity; dependency timing obeys the declared delays.
KV prediction: inference phase p95 and inference completion horizon increase.
BW prediction: those two metrics decrease.
Equal/opposite direction rejects that metric's prediction; preserve negative/mixed results.
A larger queue peak alone does not prove worse latency. Do not expect end-to-end time to halve because fixed compute remains.
Evidence: phase timings from task_statistics and canonical completion events; per-packet core egress matched to PortTrace; QueueTrace; per-task source manifest.

## Checkpoint Policy
continue_full_matrix, sequential cases. No additional manual approval gates.
Safety stop on missing inputs, file collision, nonzero exit, incomplete tasks, corrupt trace or resource exhaustion.
Record mismatches without changing predictions.

## Artifact Contract
Package: experiment-plan.md, matrix.yaml, command-manifest.yaml, run-ledger.md, cases/*/experiment-spec.md.
Each case: generate_topology.py, node.csv, topology.csv, routing_table.csv, traffic.csv, flow_manifest.csv, network_attribute.txt, parameter-resolution.json.
Execution: run-output.txt, canonical.rank0.txt, runlog, output/task_statistics.csv, execution.json.
Analysis under /home/lry/a100_ep_runtime/第11轮_依赖驱动多组推理与KV竞争_20261008: analysis/* CSV/JSON, reports/report.md, visualizations/* PNG/SVG.
Metrics: ns3 task completion(us), last ACK(us), phase duration(us), per-direction data wire bandwidth(Gbps), utilization(%), settled queue state(KiB).
Data bandwidth excludes ACK/control, includes observed data-packet headers; no moving-average curve used as measurement.
Queue: sum VOQ+egress trace state at last update of each timestamp; retain callback peak separately.
Figure labels must include source, observation point, quantity/unit and synthetic/profile/simulation distinction.

## Script Contract
Publication uses tools/publish_snapshot.py to copy only this experiment to /home/lry/llmserving-cosim-sphinx/research/round11 and source/_static/round11, consistent with the requested shared GitHub reports. Raw trace files remain local; their hashes are recorded. No simulator core or historical results are modified.
Entrypoint: /home/lry/a100_ep_runtime/第11轮_依赖驱动多组推理与KV竞争_20261008/tools/prepare_cases.py; inputs: this matrix + old Block24 flow_manifest and middle_block_events; outputs: declared case dirs and /home/lry/a100_ep_runtime/第11轮_依赖驱动多组推理与KV竞争_20261008/source snapshots.
Generation refuses existing traffic.csv; never runs simulations or changes predictions.
Run entrypoint: /home/lry/a100_ep_runtime/第11轮_依赖驱动多组推理与KV竞争_20261008/tools/run_cases.py; only matrix cases; refuses existing execution.json/runlog.
Analysis scripts may regenerate derived analysis/figures only; raw outputs never overwritten.
Simulator core and unrelated dirty files are forbidden edit targets.

## Startup Readiness
ns3 launcher, topology/traffic/parser tools, build, cmake-cache and smoke case exist.
Current dirty ub-transaction.cc/.h and historical scratch directories are preserved.
Runtime source confirms ScheduleTasks applies task.delay relative to READY, with explicit 20ns completion visibility.

## Analysis Notes
All three cases completed and classified matched. KV: inference end +16.8306%, phase p95 +64.0085%; core 100->200 Gbps with KV: inference end -29.3441%, phase p95 -67.9553%.
Full metric definitions, limitations and evidence: /home/lry/a100_ep_runtime/第11轮_依赖驱动多组推理与KV竞争_20261008/reports/report.md and analysis/audit.json. Eight regression checks pass, including actual fixed-control inputs.
Next bounded question: calibrate task-complete vs last-ACK vs real GPU/CCL completion; current replay is not real serving traffic.

### Display-window amendment after execution (not a changed simulation)
kv_100 inference completes at 8170.35442 us and final ACK at 8170.48052 us,
past the proposed 8000 us display window. Extend derived figures to 8400 us
to include the full tail. Raw runs, inputs, matrix and predictions are unchanged.
