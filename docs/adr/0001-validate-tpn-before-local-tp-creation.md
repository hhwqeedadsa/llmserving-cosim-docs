# Validate TPN before local TP creation

MTP must not rely on cross-LP zero-delay control-plane events to create peer TP channel endpoints. Receiver-side TP channel endpoints may be created locally when a packet arrives only if the packet's TPN validates against a TP channel key owned by that receiver; otherwise the TPN is an invalid simulator state.

TP channel setup is modeled as out-of-band resource preparation before traffic starts. A simulation should either load explicit TP channels from configuration or reserve the TP channels implied by the traffic workload before packets are released. TP reservation is the default semantic path for both single-threaded and MTP execution, not an MTP-only workaround. Runtime packet arrival is not a TP negotiation mechanism, and missing reservation is an error in both single-threaded and MTP execution.

Programmatic traffic that is not loaded from `traffic.csv` must perform explicit TP reservation before calling into the traffic path. If it does not, the simulator should fail instead of falling back to legacy on-demand TP creation.

**Considered Options**

- Precreate every TP endpoint before traffic starts: simple ordering semantics, but high memory cost and unwanted allocator participation for unused TP channels.
- Keep remote on-demand TP creation: low memory cost, but it can violate MTP lookahead and reorder same-time packet generation through different allocator state.
- Validate and create locally on first packet: preserves on-demand allocation without cross-LP TP creation, while keeping invalid TPNs as hard errors.

**Consequences**

The sender may resolve and create only its local TP endpoint. The receiver must distinguish a reserved TPN from an invalid TPN before accepting a packet.

When a packet uses a CNA network header, SCNA and DCNA are the spec-backed source and destination network addresses; if they are Port CNAs, they can validate the source and destination UB ports. When a packet uses the IP address format network header, the IP source and destination addresses can likewise identify UB Controller ports if the simulator assigns per-port IP addresses. UDP source and destination ports are not UB port identities in the UB specification: for UDP-carried UB traffic, the UDP destination port is the UB service port and the UDP source port may be a load-balance factor.

RTP/URMA packets in ns-3-UB use the IP address format network header, not CNA headers. They should use per-port IP addresses so the receiver can validate local and peer UB ports from the IP source and destination addresses. Their receiver-side validation must not treat UDP ports as spec-backed UB port evidence.

**Implementation Plan**

Keep TP reservation separate from TP object materialization:

1. Keep explicit `transport_channel.csv` as a reservation input. Each row records the two local endpoint views of one TP channel. Loading this file may still preload local TP objects for owned endpoints.
2. For traffic-driven automatic setup, every process reserves the same TP channel records while loading `traffic.csv`, before any task is scheduled. Reservation does not materialize either endpoint; the source owner creates its sender endpoint when the task resolves TP choices, and the receiver owner creates its endpoint locally on first valid packet arrival.
3. When a receiver sees a packet for a missing local TPN, look up that TPN in its local TP connection manager. If the TPN is reserved for the local node, create only the local endpoint object in that node's controller and process the packet. If the TPN is not reserved, fail the simulation.
4. Validate the peer/local TPN pair, per-port IP source and destination addresses, and packet VL/priority against the local channel key before accepting either an existing or lazy-materialized TP endpoint.
5. Use the IP source and destination addresses carried by RTP/URMA packets as the network-address evidence for UB ports. Do not infer UB port identity from UDP source or destination ports.
6. Keep automatic reservation bounded by unique channel records, not by every traffic row. Repeated traffic rows for the same peer and priority should reuse the same reserved channel records.

This avoids precreating every TP object, while also removing the MTP-unsafe remote `Time(0)` TP creation path.

**Acceptance Tests**

- `UbController::CreateTp` uses `NodeIdToIp(node, port)` for RTP/URMA source and destination IP addresses.
- Explicit `transport_channel.csv` loading preloads both endpoint TP objects when the endpoints are locally owned.
- Traffic-driven TP resolution creates the sender endpoint and reserves the receiver endpoint, but does not materialize the receiver endpoint before packet arrival.
- MPI and hybrid runs without `transport_channel.csv` establish identical receiver reservations on every rank before traffic starts.
- A reserved receiver TPN can be materialized locally on the receiver and then becomes visible through `GetTpByTpn`.
- An unreserved receiver TPN remains an error instead of falling back to local or remote TP creation.
- The legacy, default-disabled `RemoveUselessTp` option does not relax inbound TPN validation or turn an unknown TPN into a silent drop.
- A packet with a reserved destination TPN but a mismatched peer TPN, per-port IP address, or priority remains an error and does not materialize a receiver endpoint.
- Looking up a missing TPN does not mutate the controller's TPN map by inserting a null entry.
- UDP source and destination ports are not used as UB port identity in the RTP/URMA validation path.
- Repeated TP resolution for the same peer and priority must reuse the existing reservation instead of adding duplicate automatic TP channels.
