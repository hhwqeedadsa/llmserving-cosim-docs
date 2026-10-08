# Experiment Spec: no_kv_100

## Planning Mode
experiment-group; common source of truth: ../../experiment-plan.md and ../../matrix.yaml.

## Goal
Measure overlapping dependency-driven inference communication with no KV at 100 Gbps core rate.

## Topology / Topology Realization
custom-graph; hosts0..7, switches8/9. Hosts0..3 left and4..7 right; 400Gbps/20ns access; 100Gbps/50ns core. New bounded topology script uses library and writes directly here.

## Workload
3 groups, 10 dependency-driven Block24-template repetitions each; 240 inference tasks; 0 KV tasks. Exact bytes, delays, mappings and release schedule: common plan.

## Routing Intent
HASH deterministic compressed shortest paths, no spray/multipath.

## Network Overrides
CBFC; CC=false; retrans=false; allocation1ns; input10ns; transport RTP.

## Transport Channel Mode
on-demand; no transport_channel.csv required.

## Observability
detailed preset + canonical START/COMPLETE events.

## Startup Readiness
Verified shared runtime and toolchain; full catalog snapshot required.

## Execution Record
Success, return code 0; exact command and timing in ../../run-ledger.md and local execution.json. Raw runlog and output/task_statistics.csv present.

## Validation Notes
Synthetic replica arrivals and KV. Profile-derived compute delays, projected ring phases. Not real hardware or online cosimulation. Completion differs from final ACK.

## Analysis Notes
240 tasks / 15600 data packets; inference_end=6993.33270 us; phase p95=67.519491 us; matched as valid control.
All task payloads, canonical events, dependency delays, port serialization intervals and bandwidth integrals checked. Evidence is ns3-simulated/trace-derived; no hardware accuracy claim.
