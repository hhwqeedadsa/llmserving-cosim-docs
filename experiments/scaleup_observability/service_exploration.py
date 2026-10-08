"""Declared physical egress interventions, independent of prior run directories."""
from pathlib import Path
import shutil
import yaml

from .common import HERE, digest, read, require, save, sources
from .inputs import REFERENCE, make_input
from .slots import set_slots

PORTS = {'l1_up': (8,4), 'l2_down': (12,0), 'access_down': (8,0)}
NORMAL_ROUND_NS = 56030
WINDOW = (NORMAL_ROUND_NS//4, 3*NORMAL_ROUND_NS//4)
NOMINAL_RATE = 224_000_000_000


def selected_ports(topology):
    kinds = {n['node_id']:n['kind'] for n in topology['nodes']}
    edges = [(l[a],l[a+'_port'],l[b],l['rate_bps']) for l in topology['links']
             for a,b in (('src','dst'),('dst','src'))]
    l1 = {t for s,p,t,r in edges if kinds[s]=='DEVICE'}
    choices = {name:[] for name in PORTS}
    for s,p,t,r in edges:
        name = ('access_down' if s in l1 and kinds[t]=='DEVICE' else
                'l1_up' if s in l1 and kinds[t]=='SWITCH' and t not in l1 else
                'l2_down' if kinds[s]=='SWITCH' and s not in l1 and t in l1 else None)
        if name: choices[name].append((s,p,r))
    require(all(choices.values()), 'Need three topology roles')
    return {name:min(rows) for name,rows in choices.items()}


def prepare(output):
    output = Path(output).resolve()
    require(not output.exists(), 'Service batch output exists')
    output.mkdir(parents=True)
    service,layers,counts = make_input(4,8)
    conditions = [('normal',None,100)] + [(name,port,pct) for name,port in PORTS.items() for pct in (80,50)]
    cases = []
    for name,port,pct in conditions:
        identity = f's4-b8-{name}-{pct}'
        folder = output/'inputs'/identity
        shutil.copytree(REFERENCE,folder)
        save(folder/'service.json',service)
        save(folder/'layers.json',set_slots(layers,2))
        scenario = set_slots(yaml.safe_load((folder/'scenario.yaml').read_text()),2)
        intervals = [] if port is None else [dict(node_id=port[0],port_id=port[1],
            start_ns=WINDOW[0],end_ns=WINDOW[1],rate_bps=NOMINAL_RATE*pct//100)]
        scenario['port_service']['intervals'] = intervals
        (folder/'scenario.yaml').write_text(yaml.safe_dump(scenario,sort_keys=False))
        observation = read(folder/'observation.json')
        observation.update(schema_version='moe-ub-observation-config/4',send_state=dict(
            schema_version='moe-ub-send-state-config/1',max_records_per_rank=100000,max_total_records=1000000))
        save(folder/'observation.json',observation)
        cases.append(dict(case_id=identity,input_set=4,requests_per_rank=8,request_count=64,
            send_slots_per_rank=2,service_role=name,rate_percent=pct,port_service=intervals,
            expert_inputs=counts,directory=str(folder.relative_to(output)),
            files={p.name:digest(p) for p in sorted(folder.iterdir()) if p.is_file()}))
    method = 'service-method.md'
    save(output/'batch.json',dict(schema_version='scaleup-exploration-batch/1',study='port-service',
        synthetic_input=True,simulation_executed=False,implementation=sources(),cases=cases,
        method='experiments/scaleup_observability/'+method,method_sha256=digest(HERE/method)))
    return dict(output=str(output),study='port-service',cases=[c['case_id'] for c in cases])
