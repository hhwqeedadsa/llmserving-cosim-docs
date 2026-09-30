# Long-window contention experiment

## Goal

Generate an ns-3-backed, millisecond-scale traffic trace with visible busy and idle periods while preserving the same controlled two-switch bottleneck used by the Block 24 experiment.

## Topology

- Hosts 0--3 connect to switch 8 at 400 Gbps; hosts 4--7 connect to switch 9 at 400 Gbps.
- Switches 8 and 9 share one bidirectional 100 Gbps core link.
- All paths are deterministic shortest paths; packet spray and retransmission are disabled.

## Traffic schedule

- Ten release waves span 20--1020 us.
- Wave types include target-only, same-direction target+EP, target+reverse-EP, target+KV, balanced four-peer dispatch, partial EP competition, mixed same/reverse-direction contention, and KV-only drain.
- Target payload 278,528 B, EP payloads 139,264/278,528 B, KV payload 524,288 B, and balanced peer payload 69,632 B reuse the registered round-09 sizes.

## Evidence boundary

- Release times are a controlled synthetic replay designed to expose long-window burstiness; they are not an observed production request-arrival trace.
- Packet, port, queue, task, and ACK timings are `ns3-simulated/trace-derived`.
- The 100/400 Gbps topology is representative and uncalibrated; it does not claim A100, NCCL/HCCL, or Huawei supernode performance.

## Execution record

- Status: success; 20/20 tasks completed with full packet timestamps.
- Simulated interval: 0--1.1 ms; 1,302 data packets reconstructed at the core link.
- Maximum per-direction bandwidth: L→R 100.0 Gbps, R→L 98.2032 Gbps in 5-us bins.
- Raw core queue peaks: L→R 670.3 KiB, R→L 208.4 KiB.
