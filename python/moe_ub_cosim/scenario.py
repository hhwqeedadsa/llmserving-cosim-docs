"""Resolve explicit service and fabric scenarios against the loaded UB backend."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import yaml

from .plan import integer, require, validate_config
from .service import run as run_service

ROOT = Path(__file__).resolve().parents[2]
PROFILE_FIELDS = ('programming_model', 'operation', 'transport', 'initiator', 'payload_direction',
                  'receiver_ready', 'sender_release')
NETWORK_FILES = ('node.csv', 'topology.csv', 'routing_table.csv', 'transport_channel.csv')


class UniqueLoader(yaml.SafeLoader):
    """Do not silently let a second YAML key replace an earlier selection."""
    pass


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        require(isinstance(key, str) and key not in result, 'Non-string or duplicate YAML key')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def read_yaml(path):
    result = yaml.load(path.read_text(), Loader=UniqueLoader)
    require(isinstance(result, dict), 'Require a YAML mapping')
    return result


def fields(obj, names, label):
    require(isinstance(obj, dict) and set(obj) == set(names.split()), 'Invalid ' + label + ' fields')


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scalar(value):
    require(type(value) in (str, int, float, bool), 'Backend override must be a scalar')
    if type(value) is bool:
        return str(value).lower()
    text = str(value)
    require(text and not any(c in text for c in '\n\r"\\'), 'Invalid parameter text')
    return text


def csv_rows(path, header):
    with path.open(newline='') as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames == header.split(','), 'Unsupported CSV header: ' + str(path))
        result = list(reader)
    require(all(None not in row and all(v is not None for v in row.values()) for row in result), 'Malformed CSV row')
    return result


def csv_uint(value, label):
    require(re.fullmatch('[0-9]+', value) is not None, 'Invalid CSV ' + label)
    return integer(int(value), label, 0, 2**32-1)


def resolve_topology(config, node_rows, link_rows, ranks, mapping):
    defaults = config['defaults']
    fields(defaults, 'rate_bps delay_ns allocation_ns forward_ns', 'network defaults')
    for key, value in defaults.items():
        integer(value, key, 1 if key == 'rate_bps' else 0, 2**63-1)
    nodes = {}
    for row in node_rows:
        ids = row['nodeId'].split('..')
        require(len(ids) in (1, 2), 'Invalid node range')
        start, end = csv_uint(ids[0], 'node'), csv_uint(ids[-1], 'node')
        require(start <= end < 4096, 'Small scenario node budget is 4096')
        ports = integer(csv_uint(row['portNum'], 'port count'), 'ports', 1, 128)
        require(row['nodeType'] in ('DEVICE', 'SWITCH'), 'Invalid node kind')
        for identity in range(start, end+1):
            require(identity not in nodes, 'Duplicate node')
            nodes[identity] = dict(node_id=identity, ports=ports, kind=row['nodeType'],
                                  allocation_ns=defaults['allocation_ns'], forward_ns=defaults['forward_ns'])
    require(sorted(nodes) == list(range(len(nodes))) and nodes, 'UB node IDs must be contiguous from zero')
    require(len(ranks) == len(mapping) and len(set(mapping)) == len(mapping) and
            all(n in nodes and nodes[n]['kind'] == 'DEVICE' for n in mapping), 'Mapped endpoint absent or not a DEVICE')
    require(isinstance(config['nodes'], list) and isinstance(config['links'], list), 'Overrides must be lists')
    seen = set()
    for override in config['nodes']:
        fields(override, 'node_id allocation_ns forward_ns', 'node override')
        identity = integer(override['node_id'], 'override node', 0, 4095)
        require(identity in nodes and identity not in seen, 'Duplicate or unknown node override')
        seen.add(identity)
        for key in ('allocation_ns', 'forward_ns'):
            nodes[identity][key] = integer(override[key], key)
    links, ports = {}, {}
    def endpoints(src, sport, dst, dport):
        for n, p in ((src, sport), (dst, dport)):
            require(n in nodes and 0 <= p < nodes[n]['ports'], 'Link endpoint outside topology')
        require(src != dst, 'Self link')
        return (src, sport, dst, dport) if src < dst else (dst, dport, src, sport)
    for row in link_rows:
        a, ap, b, bp = endpoints(*(csv_uint(row[k], k) for k in ('nodeId1','portId1','nodeId2','portId2')))
        require((a,ap) not in ports and (b,bp) not in ports, 'Duplicate connected port')
        ports[a,ap] = b,bp; ports[b,bp] = a,ap
        links[a,ap,b,bp] = dict(src=a,src_port=ap,dst=b,dst_port=bp,
                               rate_bps=defaults['rate_bps'],delay_ns=defaults['delay_ns'])
    require(len(ports) == sum(n['ports'] for n in nodes.values()), 'All declared ports must be connected')
    seen = set()
    for override in config['links']:
        fields(override, 'src src_port dst dst_port rate_bps delay_ns', 'link override')
        values = [integer(override[k], k, 0, 2**32-1) for k in ('src','src_port','dst','dst_port')]
        key = endpoints(*values)
        require(key in links and key not in seen, 'Duplicate or unknown link override')
        seen.add(key)
        links[key].update(rate_bps=integer(override['rate_bps'],'link rate',1),
                          delay_ns=integer(override['delay_ns'],'link delay'))
    return nodes, links, ports


def resolve_network(config, directory, ranks, mapping, endpoint_groups=None):
    fields(config, 'case_path plane_id defaults nodes links', 'network')
    require(isinstance(config['plane_id'], str) and config['plane_id'], 'Explicit single plane identity required')
    base = (directory/config['case_path']).resolve()
    require(base.is_dir(), 'Missing network case')
    nodes, links, ports = resolve_topology(config,
        csv_rows(base/'node.csv', 'nodeId,nodeType,portNum,allocationDelay,forwardDelay'),
        csv_rows(base/'topology.csv', 'nodeId1,portId1,nodeId2,portId2,bandwidth,delay'), ranks, mapping)
    routes = csv_rows(base/'routing_table.csv', 'nodeId,dstNodeId,dstPortId,outPorts,metrics')
    routing = {}
    for row in routes:
        node, dst, dport, port, metric = [csv_uint(row[k], k) for k in ('nodeId','dstNodeId','dstPortId','outPorts','metrics')]
        require(node in nodes and dst in nodes and (node,port) in ports and (dst,dport) in ports and metric > 0,
                'Invalid routing endpoint or metric')
        key = node,dst,dport
        require(key not in routing, 'Only one explicit route per destination is verified in this small scenario entry')
        routing[key] = port
    channels = csv_rows(base/'transport_channel.csv', 'nodeId1,portId1,tpn1,nodeId2,portId2,tpn2,priority,metric')
    pairs, tp_ids = set(), set()
    for row in channels:
        a,ap,at,b,bp,bt,priority,metric = [csv_uint(row[k],k) for k in row]
        require(a in nodes and b in nodes and nodes[a]['kind']==nodes[b]['kind']=='DEVICE' and a!=b and
                (a,ap) in ports and (b,bp) in ports and priority==7 and metric>0, 'Invalid precreated channel')
        require((a,at) not in tp_ids and (b,bt) not in tp_ids and tuple(sorted((a,b))) not in pairs,
                'Duplicate precreated TP or endpoint pair')
        tp_ids.update(((a,at),(b,bt)));pairs.add(tuple(sorted((a,b))))
        for src,srcport,dst,dstport in ((a,ap,b,bp),(b,bp,a,ap)):
            current = src; visited = set()
            while current != dst:
                require(current not in visited and (current,dst,dstport) in routing, 'Loop or missing route')
                visited.add(current); port = routing[current,dst,dstport]
                require(current != src or port == srcport, 'Channel source port differs from route')
                current, incoming = ports[current,port]
            require(incoming == dstport, 'Route reaches wrong destination port')
    domains = [mapping] if endpoint_groups is None else endpoint_groups
    require(all(domains) and sorted(n for group in domains for n in group)==sorted(mapping),
            'Channel domains must partition mapped endpoints')
    require(all(tuple(sorted((a,b))) in pairs for group in domains for a in group for b in group if a!=b),
            'Precreated channels must cover mapped endpoint pairs')
    topology = dict(nodes=[nodes[k] for k in sorted(nodes)],links=[links[k] for k in sorted(links)])
    return base, topology, routes, channels


def write_network(path, topology, routes, channels, backend):
    path.mkdir()
    def write(name, header, rows):
        with (path/name).open('w', newline='') as stream:
            writer=csv.writer(stream,lineterminator='\n');writer.writerow(header.split(','));writer.writerows(rows)
    write('node.csv','nodeId,nodeType,portNum,allocationDelay,forwardDelay',
          ((n['node_id'],n['kind'],n['ports'],str(n['allocation_ns'])+'ns',str(n['forward_ns'])+'ns') for n in topology['nodes']))
    write('topology.csv','nodeId1,portId1,nodeId2,portId2,bandwidth,delay',
          ((l['src'],l['src_port'],l['dst'],l['dst_port'],str(l['rate_bps'])+'bps',str(l['delay_ns'])+'ns') for l in topology['links']))
    for name, rows, header in [
        ('routing_table.csv', routes, 'nodeId,dstNodeId,dstPortId,outPorts,metrics'),
        ('transport_channel.csv', channels, 'nodeId1,portId1,tpn1,nodeId2,portId2,tpn2,priority,metric')]:
        write(name,header,(list(r.values()) for r in rows))
    # Complete resolved snapshot, including inactive defaults; never inherit the
    # base case's network_attribute.txt. JSON below is the authoritative input.
    with (path/'network_attribute.txt').open('w') as stream:
        for kind, prefix in [('attributes','default'),('globals','global')]:
            for key,value in sorted(backend[kind].items()):stream.write(f'{prefix} {key} "{value}"\n')
    save(path/'backend.json',backend)


def prepare(input_path, backend_binary, output):
    config=read_yaml(input_path)
    require(config.get('schema_version') in ('moe-ub-scenario-run/1','moe-ub-scenario-run/2','moe-ub-scenario-run/3'),
            'Use an executable scenario schema; reference specifications are not executable')
    service_intervals=config['schema_version']=='moe-ub-scenario-run/3'
    multi_plane = config['schema_version']=='moe-ub-scenario-run/2' or (service_intervals and 'fabric_file' in config['network'])
    fields(config,'schema_version scenario_id basis service layer system backend network execution observation max_simulation_ns'+
           (' port_service' if service_intervals else ''), 'scenario')
    require(config['basis']=='selected_reference' and re.fullmatch('[A-Za-z0-9_.-]+',config['scenario_id']), 'Invalid scenario identity/basis')
    directory=input_path.parent
    paths={k:(directory/config[k]).resolve() for k in ('service','layer','system')}
    require(all(p.is_file() for p in paths.values()), 'Missing scenario input')
    service=json.loads(paths['service'].read_text());layer=json.loads(paths['layer'].read_text())
    require(service.get('schema_version') in ('moe-ub-configured-service/1','moe-ub-configured-service/2','moe-ub-configured-service/3','moe-ub-configured-service/4','moe-ub-evidence-service/1'),
            'Scenario requires configured service')
    fields(config['execution'],'chunk_bytes send_slots_per_rank seed run','execution')
    execution=config['execution']
    integer(execution['chunk_bytes'],'chunk_bytes',1,2**32-1);integer(execution['send_slots_per_rank'],'send slots',1,2)
    integer(execution['seed'],'seed',1,2**32-1);integer(execution['run'],'run',1)
    integer(config['max_simulation_ns'],'max_simulation_ns',1)
    # The scope/capacity checks happen before a backend process can execute work.
    evidence = service['schema_version']=='moe-ub-evidence-service/1'
    full = service['schema_version']=='moe-ub-configured-service/4' or (evidence and service['service']['schema_version']=='moe-ub-configured-service/4')
    composed = evidence or full or service['schema_version']=='moe-ub-configured-service/3'
    if composed:
        if full:
            from .full_model_service import FullModelService as ComposedService
        else:
            from .composed_service import ComposedService
        for group in layer['groups']:
            for current in group['layers']:
                current.update(chunk_bytes=execution['chunk_bytes'],send_slots_per_rank=execution['send_slots_per_rank'])
        if evidence:
            if full:
                from .full_model_service import FullModelEvidenceService as EvidenceService
            else:
                from .evidence_service import EvidenceService
            frontend=EvidenceService(service,layer)
        else:
            frontend=ComposedService(service,layer)
    else:
        from .configured_service import ConfiguredService
        layer.update(chunk_bytes=execution['chunk_bytes'],send_slots_per_rank=execution['send_slots_per_rank'])
        validate_config(layer)
        frontend=ConfiguredService(service,layer)
    mapping={r:n for group in frontend.groups for r,n in zip(group['rank_ids'],group['rank_to_ub_node'])}
    selection=config['backend']
    fields(selection,'reference_file profile_by_role attributes globals tp_max_data_payload_bytes transport_channel_setup','backend selection')
    reference=(directory/selection['reference_file']).resolve();reference_data=read_yaml(reference)
    require(reference_data.get('schema_version')=='moe-ub-scenario-reference/0.1', 'Unsupported backend reference schema')
    ref=reference_data['ns3_backend']
    fields(selection['profile_by_role'],'dispatch combine','profile selection')
    profiles={}
    for role,name in selection['profile_by_role'].items():
        require(isinstance(name,str) and name in ref['transaction_profiles'],'Missing active profile')
        chosen=ref['transaction_profiles'][name]
        require(isinstance(chosen,dict) and all(k in chosen for k in PROFILE_FIELDS),'Active profile is not implemented')
        profiles[role]={k:chosen[k] for k in PROFILE_FIELDS}
    domains=[group['rank_to_ub_node'] for group in frontend.groups] if composed else None
    fabric_spec = fabric_audit = None
    if multi_plane:
        from . import fabric
        require(composed, 'Fabric scenarios require explicit configured groups and layer segments')
        fields(config['network'],'fabric_file defaults nodes links','fabric network')
        fabric_path=(directory/config['network']['fabric_file']).resolve()
        fabric_spec=json.loads(fabric_path.read_text())
        topology,routes,channels,fabric_audit=fabric.generate(fabric_spec,config['network']['defaults'])
        fabric.check_groups(fabric_spec,frontend.groups,layer)
        rows=fabric.tables(topology,routes,channels)
        def dictionaries(name):return [dict(zip(fabric.HEADERS[name].split(','),map(str,r))) for r in rows[name]]
        nodes,links,_=resolve_topology(config['network'],dictionaries('node.csv'),dictionaries('topology.csv'),
                                       frontend.ranks,[mapping[r] for r in frontend.ranks])
        topology=dict(nodes=list(nodes.values()),links=[links[k] for k in sorted(links)])
        network_sources=[fabric_path,Path(fabric.__file__)]
    else:
        base,topology,routes,channels=resolve_network(config['network'],directory,frontend.ranks,[mapping[r] for r in frontend.ranks],domains)
        network_sources=[base/name for name in NETWORK_FILES]
    if service_intervals:
        from .port_service import validate as validate_port_service
        validate_port_service(config['port_service'],topology,config['max_simulation_ns'])
    output.mkdir(parents=True,exist_ok=False)
    commands=[]
    def invoke(label,argv):
        start=datetime.now(timezone.utc).isoformat()
        try:
            result=subprocess.run([str(v) for v in argv],cwd=ROOT,capture_output=True,timeout=60)
            code,stdout,stderr=result.returncode,result.stdout,result.stderr
        except subprocess.TimeoutExpired as error:
            code,stdout,stderr=None,error.stdout or b'',error.stderr or b''
        for field,value in [('stdout',stdout),('stderr',stderr)]:
            (output/(label+'.'+field+'.log')).write_bytes(value)
        commands.append(dict(argv=[str(v) for v in argv],cwd=str(ROOT),started_utc=start,
            ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=code,timed_out=code is None))
        save(output/'resolve_commands.json',commands)
        require(code==0,f'Backend {label} failed (exit={code}): '+stderr.decode('utf-8',errors='replace'))
        return json.loads(stdout)
    catalog=invoke('catalog',[backend_binary,'--backend-catalog'])
    require(catalog['ub_revision']==ref['revision'], 'Backend/reference dependency revision mismatch')
    values,origins={},{}
    for kind in ('attributes','globals'):
        values[kind]={k:v['default'] for k,v in catalog[kind].items()}
        origins[kind]={k:[dict(source='runtime_catalog',value=v)] for k,v in values[kind].items()}
        for label,overlay in [('reference',ref['reference_'+kind]),('scenario',selection[kind])]:
            require(isinstance(overlay,dict),'Overrides must be a mapping')
            for key,value in overlay.items():
                require(key in values[kind], 'Unknown backend parameter '+key)
                value=scalar(value);values[kind][key]=value
                origins[kind][key].append(dict(source=label,value=value))
        for key,value in catalog['fixed_'+kind].items():
            # An explicit contradictory selection is an error, not a silently lost override.
            for overlay in (ref['reference_'+kind],selection[kind]):
                require(key not in overlay or scalar(overlay[key])==value, 'Unsupported joint setting '+key)
            values[kind][key]=value;origins[kind][key].append(dict(source='joint_constraint',value=value))
    requested=dict(schema_version='moe-ub-backend-config/1',scenario_id=config['scenario_id'],catalog=catalog,
        profiles=profiles,execution=execution,tp_max_data_payload_bytes=selection['tp_max_data_payload_bytes'],
        transport_channel_setup=selection['transport_channel_setup'],topology=topology,origins=origins,**values)
    if multi_plane:
        requested.update(schema_version='moe-ub-backend-config/2',fabric=fabric_spec)
        if any(p['programming_model']=='native_memory' for p in profiles.values()):
            fabric_audit['routing']=dict(programming_model='native_memory',
                source_selection='HASH over primary-node shortest candidates across planes',
                source_ports=list(range(fabric_spec['shape']['planes'])),
                switch_selection='HASH over reachable shortest candidates within the selected plane',
                packet_spray=values['attributes']['ns3::UbLdstApi::UsePacketSpray']=='true',
                use_shortest_paths=values['attributes']['ns3::UbLdstApi::UseShortestPaths']=='true',
                packet_spray_salt='native 8-bit incrementing packet field, echoed by response',
                transport_channels_used=False,inactive_devices_have_routes=True,
                validation='declaration; actual route tables checked in effective_backend.json')
        save(output/'fabric-audit.json',fabric_audit)
    if service_intervals:
        requested.update(schema_version='moe-ub-backend-config/3',port_service=config['port_service'])
    request=save(output/'requested_backend.json',requested)
    resolved=invoke('check',[backend_binary,'--check-backend',request])
    write_network(output/'network',topology,routes,channels,resolved)
    save(output/'service.json',service);save(output/'layer.json',layer)
    shutil.copyfile(paths['system'],output/'system.json');shutil.copyfile(input_path,output/'scenario.yaml')
    observation=None
    if config['observation'] is not None:
        require(isinstance(config['observation'],dict), 'Observation must be a mapping or explicit null')
        observation=save(output/'observation.json',config['observation'])
    source_paths=[input_path,reference,*paths.values(),*network_sources,ROOT/'deps.lock.json']
    manifest=dict(schema_version='moe-ub-scenario-resolution/1',scenario_id=config['scenario_id'],
        state='resolved_not_executed',calibration_status='unvalidated_reference',
        execution_scope=('configured_groups_full_model_decode_reference' if full else
            'configured_groups_moe_layer_segment_continuous_batching' if composed else
            'configured_single_group_single_moe_layer_continuous_batching'
            if service['schema_version']=='moe-ub-configured-service/2' else
            'configured_single_group_single_moe_layer_one_request_per_rank'),
        imported_reference_sections=['ns3_backend.reference_attributes','ns3_backend.reference_globals','ns3_backend.transaction_profiles'],
        reference_model_and_target_topology_imported=False,target_replication_claimed=False,
        plane_id=config['network'].get('plane_id'),network_override_order=['base_case_connectivity','scenario_network_defaults','scenario_node_link_overrides'],
        network_defaults=config['network']['defaults'],node_overrides=config['network']['nodes'],link_overrides=config['network']['links'],
        base_case_attribute_file_used=False,authoritative_network_configuration='network/backend.json',
        backend=dict(path=str(backend_binary),sha256=sha(backend_binary),ub_revision=catalog['ub_revision']),
        sources=[dict(path=str(p),sha256=sha(p)) for p in source_paths],
        adapter_sha256=sha(Path(__file__)),
        fixed_dependencies=json.loads((ROOT/'deps.lock.json').read_text()),
        active_profile_by_role=selection['profile_by_role'],inactive_profiles_checked=False,
        packetization=dict(collective_chunk_bytes=execution['chunk_bytes'],
            ta_segment_bytes=int(resolved['attributes']['ns3::UbJetty::TaSegmentBytes']),
            tp_max_data_payload_bytes=resolved['tp_max_data_payload_bytes'],tp_payload_source='compiled_UB_MTU_BYTE'),
        observation=config['observation'])
    if multi_plane:
        manifest.update(schema_version='moe-ub-scenario-resolution/2',fabric=fabric_audit,
            network_override_order=['generated_fabric','scenario_network_defaults','scenario_node_link_overrides'])
        manifest.pop('plane_id')
    if composed:
        manifest['transport_channel_required_groups']=[dict(group_id=g['group_id'],rank_to_ub_node=g['rank_to_ub_node'])
                                                      for g in frontend.groups]
    save(output/'resolution.json',manifest)
    args=argparse.Namespace(input=output/'service.json',layer=output/'layer.json',backend=backend_binary,
        system=output/'system.json',network=output/'network',observation=observation,output_dir=output/'run',
        max_simulation_ns=config['max_simulation_ns'],wall_timeout_seconds=60)
    return args,manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('resolve','run'))
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--backend',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    output=args.output_dir.resolve()
    service_args,manifest=prepare(args.input.resolve(),args.backend.resolve(),output)
    if args.action=='run':
        try:
            run_service(service_args)
            effective=json.loads((output/'run/execution/effective_backend.json').read_text())
            require(effective['catalog']==json.loads((output/'network/backend.json').read_text())['catalog'],
                    'Effective backend catalog changed')
            executed_network=output/'run/execution/network'
            for name in NETWORK_FILES:
                require(sha(output/'network'/name)==sha(executed_network/name), 'Executed network input changed: '+name)
            manifest['executed_network_inputs']={name:sha(executed_network/name) for name in NETWORK_FILES}
            manifest.update(state='executed',normal_exit=True,effective_backend='run/execution/effective_backend.json')
        except Exception as error:
            manifest.update(state='failed',normal_exit=False,error=str(error));raise
        finally:save(output/'resolution.json',manifest)
    print(f"Scenario {manifest['scenario_id']}: {manifest['state']} at {output}")


if __name__=='__main__':main()
