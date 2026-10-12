"""A cosim/1 driver for already generated first-round graphs, without Scheduler."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import re
import selectors
import shutil
import subprocess

from .observation import backend_observation
from .plan import require
from .protocol_io import read_line
from .user_io import ROOT, inventory, read, save, timestamp


def network_input(source, output, override, binary):
    source = Path(source); actual = Path(override) if override else source
    original = inventory(source); replacement = inventory(actual)
    require(set(original) == set(replacement), 'Replay network override must keep the original files and topology')
    changed = {name for name in original if original[name] != replacement[name]}
    structured=(source/'backend.json').exists()
    require(changed <= ({'backend.json'} if structured else {'topology.csv'}),
            'Replay may change only structured port service/native spray/STORE quota, or legacy topology.csv delay')
    overrides={}
    if structured:
        before,after=read(source/'backend.json'),read(actual/'backend.json')
        def fixed(settings):
            value=deepcopy(settings);schema=value.pop('schema_version')
            require(schema in ('moe-ub-backend-config/1','moe-ub-backend-config/2','moe-ub-backend-config/3'),
                    'Unsupported replay backend schema')
            value.pop('port_service',None)
            value['attributes'].pop('ns3::UbLdstApi::UsePacketSpray',None)
            value['attributes'].pop('ns3::UbLdstThread::StoreOutstanding',None)
            return value
        require(fixed(before)==fixed(after),'Replay cannot change task profiles, endpoints, base topology or other backend parameters')
        if 'port_service' in after:
            from .port_service import validate
            validate(after['port_service'],after['topology'])
        if before.get('port_service')!=after.get('port_service'):
            overrides['port_service']=dict(before=before.get('port_service'),after=after.get('port_service'))
        key='ns3::UbLdstApi::UsePacketSpray'
        if before['attributes'][key]!=after['attributes'][key]:
            require(any(p['programming_model']=='native_memory' for p in after['profiles'].values()) and
                    after['attributes'][key] in ('true','false'),'Spray replay override requires native memory')
            overrides[key]=dict(before=before['attributes'][key],after=after['attributes'][key])
        key='ns3::UbLdstThread::StoreOutstanding'
        if before['attributes'].get(key)!=after['attributes'].get(key):
            value=after['attributes'].get(key)
            require(any(p['programming_model']=='native_memory' and p['operation']=='STORE'
                        for p in after['profiles'].values()) and isinstance(value,str) and
                    re.fullmatch(r'[1-9][0-9]*',value) and 1<=int(value)<=65536,
                    'STORE quota replay override requires native STORE and an integer string in 1..65536')
            overrides[key]=dict(before=before['attributes'].get(key),after=value)
    elif changed:
        def rows(path):
            with path.open(newline='') as stream:
                reader = csv.DictReader(stream)
                require(reader.fieldnames == ['nodeId1', 'portId1', 'nodeId2', 'portId2', 'bandwidth', 'delay'],
                        'Unsupported topology.csv header')
                return list(reader)
        before, after = rows(source/'topology.csv'), rows(actual/'topology.csv')
        require(len(before) == len(after) and all(
            {k:v for k,v in a.items() if k != 'delay'} == {k:v for k,v in b.items() if k != 'delay'} and
            re.fullmatch(r'\d+ns', b['delay']) for a, b in zip(before, after)),
            'Only topology.csv delay may change; endpoints, bandwidth and all other settings must match')
    shutil.copytree(actual, output)
    rebound = None
    if (output/'backend.json').exists():
        settings = read(output/'backend.json')
        result = subprocess.run([str(binary), '--backend-catalog'], capture_output=True, text=True, timeout=30)
        require(result.returncode == 0, 'Backend catalog inspection failed: '+result.stderr)
        catalog = json.loads(result.stdout)
        from .replay_catalog import rebind
        rebound=rebind(settings,catalog)
        for key,value in overrides.items():
            if key in settings['attributes']:
                settings['origins']['attributes'][key].append(dict(source='frozen_replay_override',value=value['after']))
        save(output/'backend.json', settings)
        checked=subprocess.run([str(binary),'--check-backend',str(output/'backend.json')],capture_output=True,text=True,timeout=30)
        require(checked.returncode==0,'Replay backend validation failed: '+checked.stderr)
    return dict(original_inventory=original,changed_files=sorted(changed),overrides=overrides,catalog_revision_rebinding=rebound)


def run(directory, manifest, shared, output, inputs, config):
    output.mkdir(exist_ok=False)
    # Bootstrap, deployment, policy and Chakra files are already sealed inputs.
    # Nothing is generated, scheduled, or imported from the original route files.
    for name in ('deployment.json', 'record-storage.json'):
        shutil.copyfile(shared/name, output/name)
    network = inputs/'network'
    change = network_input(directory/'inputs/network', network, config.get('network'), Path(config['backend']))
    shutil.copyfile(directory/'inputs/system.json', inputs/'system.json')
    observation=config.get('observation',manifest['observation'])
    max_ns=config.get('max_simulation_ns',manifest['capture_end_ns'])
    save(inputs/'observation.json', observation)
    save(inputs/'backend-observation.json', backend_observation(observation, manifest['ranks'], max_ns))
    command = [config['backend'], manifest['run_id'], str(shared), str(inputs/'system.json'),
               str(network), str(inputs/'backend-observation.json'), str(output/'execution'), str(max_ns)]
    record = dict(argv=command, cwd=str(ROOT), started_utc=timestamp(), network=change)
    child = None; decision = 0; completed = set(); now = 0
    expected = {(s['graph_id'], s['wave_id']) for s in manifest['submissions']}
    try:
        with (output/'backend.stderr.log').open('x') as error, (output/'protocol.jsonl').open('x') as transcript:
            child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=error, bufsize=0)
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ); buffer = bytearray()
                while True:
                    line = read_line(child, selector, buffer, config['backend_timeout_seconds'])
                    transcript.write(line); transcript.flush(); message = json.loads(line)
                    require(message['schema_version'] == 'cosim/1' and message['type'] == 'DECIDE' and
                            message['run_id'] == manifest['run_id'] and message['decision_id'] == decision and
                            message['now_ns'] >= now, 'Invalid replay decision identity/time')
                    now = message['now_ns']
                    if decision == 0:
                        require(now == 0 and message['completed'] == [] and message['available_rank_ids'] == manifest['ranks'],
                                'Replay did not start from the declared empty network state')
                    for done in message['completed']:
                        key = done['graph_id'], done['wave_id']
                        require(done['kind'] == 'WAVE_DONE' and key in expected and key not in completed and
                                done['finished_ns'] == now, 'Unexpected/duplicate replay graph completion')
                        completed.add(key)
                    finished = completed == expected
                    reply = dict(schema_version='cosim/1', type='COMMIT', run_id=manifest['run_id'],
                        decision_id=decision, now_ns=now, submissions=deepcopy(manifest['submissions']) if decision == 0 else [],
                        next_wakeup_ns=None, input_exhausted=True, service_finished=finished)
                    line = json.dumps(reply)+'\n'; transcript.write(line); transcript.flush()
                    child.stdin.write(line.encode()); child.stdin.flush(); decision += 1
                    if finished:
                        break
                child.stdin.close()
                code = child.wait(timeout=config['backend_timeout_seconds'])
                require(code == 0 and not buffer and child.stdout.read() == b'', 'Backend failed or returned unexpected trailing protocol')
        result = read(output/'execution/result.json')
        require(result['normal_exit'] and result['network_drain']['idle'] and result['random'] == manifest['random'],
                'Replay did not end normally with the declared random configuration')
        save(output/'service_result.json', dict(schema_version='moe-ub-frozen-front/1', service_finished=True,
            scope='driver_completed_frozen_graphs_not_request_scheduling', configured_service_executed=False,
            graph_resources=result.get('graph_resources'), completed_graphs=sorted(completed), finished_ns=now))
        return result
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
            record['exit_code'] = child.wait()
            child.stdout.close()
            if not child.stdin.closed:
                child.stdin.close()
        record['finished_utc'] = timestamp(); save(output/'command.json', record)
