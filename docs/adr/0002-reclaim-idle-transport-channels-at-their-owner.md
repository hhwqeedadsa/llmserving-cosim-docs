# Reclaim idle transport channels at their execution owner

RTP transport channel objects retain sender, receiver, retransmission, transaction, allocator, and congestion-control state. Keeping every materialized endpoint until simulation shutdown is simple, but memory grows with the number of channels used over a long run even when earlier channels are permanently idle.

The released `RemoveUselessTp` attribute attempted to remove completed channels, but its name did not describe the safety condition and direct peer destruction was not valid when endpoints belonged to different threads or MPI processes.

## Decision

Expose `ns3::TpConnectionManager::EnableIdleTransportChannelReclamation` as the canonical attribute, defaulting to `false`. Keep `RemoveUselessTp` only as a legacy alias so released cases remain compatible.

A completed task releases its Jetty-to-channel references. A transport channel becomes reclaimable only after:

- every planned task using the same endpoint pair and priority has released its use;
- no Jetty or transaction scheduling state still references the channel;
- ACK, CNP, WQE segment, unacknowledged PSN, retransmission timer, inbound transaction unit, buffered packet, remote request, or delayed congestion notification remains;
- both endpoint owners have independently reached the same idle condition.

Planned-use accounting is created only when reclamation is enabled. Every runtime participant applies the same decrement when task completion becomes globally visible, while each MPI process schedules reclamation only for nodes it owns. This keeps the connection-wide last-use decision consistent without allowing one process to mutate another process's endpoint.

Each endpoint is destroyed only by its owning execution context. The two owners coordinate with a request identifier and a four-step exchange: request, peer endpoint reclaimed, reclamation complete, and reclamation confirmed. If both endpoints request reclamation together, the lower `(nodeId, tpn)` pair is the deterministic initiator.

Local owners schedule control events with zero simulated delay. A notification that crosses a thread or MPI-process owner uses the positive dependency visibility delay inferred from UB links. No fixed empirical delay is part of the lifecycle contract. Granted Time Window MPI carries these fixed-size control events; Null Message MPI rejects them explicitly until it gains an equivalent interface.

Connection reservations remain after endpoint objects are reclaimed. A later task can materialize a new local endpoint from the validated reservation without reusing an invalid TPN or creating an endpoint on a remote owner.

## Consequences

- Existing cases preserve their lifecycle because the canonical attribute and legacy alias both default to `false`.
- Disabled reclamation does not build the planned-use accounting table.
- Enabling reclamation can substantially reduce memory when completed channels accumulate over time.
- Workloads whose channels are simultaneously active have a smaller reclaimable window and may pay control-event overhead with limited memory benefit.
- The manager owns lifecycle coordination; transport, transaction, allocator, and congestion-control modules expose only the local idle and detach operations needed to make destruction safe.
- Adding another parallel runtime requires an owner-local fixed-size control-event transport or an explicit unsupported-mode error.

## Acceptance

- Canonical and legacy attributes enable the same behavior.
- Enabling and disabling reclamation produce byte-identical semantic outputs for single-threaded, MTP, MPI, and hybrid runs that include ACKs, deterministic loss, selective retransmission, flow control, and DCQCN.
- Concurrent requests converge on one initiator and do not destroy an endpoint twice.
- A reclaimed reservation can materialize a channel again.
- Enabling reclamation reduces peak RSS for sequential unique-channel workloads, while concurrent-channel measurements document the possible runtime overhead boundary.
