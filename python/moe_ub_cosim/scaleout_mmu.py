"""Bounded final switch buffer observations; post-run evaluation only."""
from .scaleout_topology import fields, integer, require


def validate_options(config):
    if 'mmu_snapshot' not in config:
        return
    option = config['mmu_snapshot']
    fields(option, 'nodes max_ports', 'MMU snapshot')
    require(isinstance(option['nodes'], list) and 1 <= len(option['nodes']) <= 16,
            'MMU snapshot requires 1..16 explicit switch nodes')
    for node in option['nodes']:
        integer(node, 'MMU snapshot node', 0, 1279)
    require(len(set(option['nodes'])) == len(option['nodes']), 'duplicate MMU snapshot node')
    integer(option['max_ports'], 'MMU snapshot max_ports', 1, 1024)


def validate_topology(topology, config):
    validate_options(config)
    if 'mmu_snapshot' not in config:
        return
    option = config['mmu_snapshot']
    nodes = {n['id']: n for n in topology['nodes']}
    counts = {}
    for node in option['nodes']:
        require(node in nodes and nodes[node]['kind'] != 'gpu',
                'MMU snapshot node must be a switch present in topology')
        counts[node] = sum(link[side] == node for link in topology['links'] for side in ('src', 'dst'))
        require(counts[node] > 0, 'MMU snapshot switch has no physical ports')
    require(sum(counts.values()) <= option['max_ports'], 'MMU snapshot physical ports exceed max_ports')


def analyze(snapshot):
    """Check selected complete switch states without inferring past execution."""
    require(snapshot.get('schema_version') == 'native-switch-mmu-snapshot/1', 'unknown MMU snapshot schema')
    require(snapshot.get('complete_selected_switch_ports') is True, 'MMU snapshot does not cover complete selected ports')
    integer(snapshot['time_ns'], 'MMU snapshot time_ns', 0, 10**9)
    selected = snapshot['selected_nodes']
    require(isinstance(selected, list) and 1 <= len(selected) <= 16, 'invalid MMU snapshot selection')
    for node in selected:
        integer(node, 'MMU snapshot selected node', 0, 1279)
    require(len(set(selected)) == len(selected), 'duplicate MMU snapshot selection')
    switches = snapshot['switches']
    require(isinstance(switches, list) and [s['node'] for s in switches] == selected,
            'MMU snapshot switch coverage differs from selection')
    findings, totals = [], []
    port_count = 0
    for switch in switches:
        node = switch['node']
        integer(switch['reserve_bytes'], 'MMU reserve_bytes', 0, 2**32-1)
        integer(switch['shared_used_bytes'], 'MMU shared_used_bytes', 0, 2**32-1)
        ports = switch['ports']
        require(isinstance(ports, list) and 1 <= len(ports) <= 1024, 'invalid MMU snapshot ports')
        integer(switch['physical_port_count'], 'MMU physical_port_count', 1, 1024)
        require(len(ports) == switch['physical_port_count'], 'MMU snapshot physical port count mismatch')
        port_count += len(ports)
        require(port_count <= 1024, 'MMU snapshot total port budget exceeded')
        require([p['port'] for p in ports] == list(range(1, len(ports)+1)), 'incomplete MMU snapshot physical ports')
        incoming, outgoing, queued = [0]*8, [0]*8, [0]*8
        expected_shared = 0
        for port in ports:
            integer(port['port'], 'MMU physical port', 1, 1024)
            require(type(port['link_up']) is bool, 'MMU link_up must be boolean')
            for key in ('ingress_bytes', 'headroom_used_bytes', 'egress_mmu_bytes', 'queue_bytes', 'pfc_paused'):
                row = port[key]
                require(isinstance(row, list) and len(row) == 8, 'MMU snapshot needs 8 priorities')
                for value in row:
                    integer(value, 'MMU '+key, 0, 1 if key == 'pfc_paused' else 2**32-1)
            for q in range(1, 8):
                ingress, headroom = port['ingress_bytes'][q], port['headroom_used_bytes'][q]
                egress, queue = port['egress_mmu_bytes'][q], port['queue_bytes'][q]
                incoming[q] += ingress + headroom
                outgoing[q] += egress
                queued[q] += queue
                expected_shared += max(ingress-switch['reserve_bytes'], 0)
                if egress != queue:
                    findings.append(dict(kind='egress_queue_mismatch', node=node, port=port['port'],
                        priority=q, egress_mmu_bytes=egress, queue_bytes=queue))
        for q in range(1, 8):
            if incoming[q] != outgoing[q]:
                findings.append(dict(kind='ingress_egress_mismatch', node=node, priority=q,
                    ingress_and_headroom_bytes=incoming[q], egress_mmu_bytes=outgoing[q]))
        if expected_shared != switch['shared_used_bytes']:
            findings.append(dict(kind='shared_occupancy_mismatch', node=node,
                expected_shared_bytes=expected_shared, shared_used_bytes=switch['shared_used_bytes']))
        totals.append(dict(node=node, ports=len(ports), ingress_and_headroom_bytes=sum(incoming),
            egress_mmu_bytes=sum(outgoing), queued_data_bytes=sum(queued),
            shared_used_bytes=switch['shared_used_bytes']))
    return dict(scope='selected_switches_final_instant_only_evaluation', time_ns=snapshot['time_ns'],
        consistent=not findings, checked_priorities=list(range(1, 8)), switches=totals, findings=findings,
        limitations=['Priority 0 control bytes, serialization and in-flight bytes are excluded from MMU checks.',
            'Ingress, egress and shared counts overlap; they are not additive physical memory.',
            'Consistency does not prove history, drain, correct recovery, or hardware calibration.'])
