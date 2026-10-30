"""Necessary EP transport completion ordering, with zero coordination cost.

This is not a GPU visibility/CQ model or a simulation of GIN barrier signals.
"""
from .scaleout_topology import fields,require

MODE='ep_transport_flush/1'


def enabled(config):
    if 'phase_completion' not in config:return False
    fields(config['phase_completion'],'mode','phase completion')
    require(config['phase_completion']['mode']==MODE,'unsupported phase completion mode')
    return True


def structure(wave,config):
    """Validate necessary edges independently of the wave generator."""
    if not enabled(config):
        require(wave['schema_version']=='moe-scaleout-wave/1' and 'phase_completion' not in wave and
                not any(n['kind']=='GATE' for nodes in wave['rank_nodes'].values() for n in nodes),
                'undeclared phase completion in graph')
        return []
    require(wave['schema_version']=='moe-scaleout-wave/2','phase mode requires wave/2')
    spec=wave['phase_completion'];fields(spec,'mode gates','wave phase completion')
    require(spec['mode']==MODE and len(spec['gates'])==2*config['model']['layers'],'missing phase gates')
    expected={(l,p) for l in range(config['model']['layers']) for p in ('dispatch','combine')}
    seen=set();members_seen=set()
    for gate in spec['gates']:
        fields(gate,'id layer phase members','phase gate')
        key=gate['layer'],gate['phase'];require(key in expected and key not in seen,'duplicate/unknown phase gate')
        seen.add(key);layer,phase=key
        require(gate['id']==f'L{layer}.{phase}' and [m['rank'] for m in gate['members']]==wave['rank_ids'],
                'phase gate must cover the full EP group in rank order')
        for member in gate['members']:
            fields(member,'rank node','phase member');rank=member['rank']
            nodes=wave['rank_nodes'].get(rank,wave['rank_nodes'].get(str(rank)))
            by_id={n['id']:n for n in nodes};by_name={n['name']:n for n in nodes}
            task=by_id[member['node']]
            require((rank,task['id']) not in members_seen,'node reused across phase gates')
            members_seen.add((rank,task['id']))
            producer=by_name[f'L{layer}.'+('pack' if phase=='dispatch' else 'experts')]
            required={n['id'] for n in nodes if n['kind'] in ('SEND','RECV') and
                      n['message']['layer']==layer and n['message']['phase']==phase}|{producer['id']}
            require(task['kind']=='GATE' and task['duration_ns']==0 and task['name']==f'L{layer}.{phase}.flush' and
                    set(task['deps'])==required and len(task['deps'])==len(required),'phase gate omitted local completion')
            consumer=by_name[f'L{layer}.'+('experts' if phase=='dispatch' else 'merge')]
            require(task['id'] in consumer['deps'],'phase consumer omitted EP completion gate')
    require(members_seen=={(int(rank),n['id']) for rank,nodes in wave['rank_nodes'].items() for n in nodes if n['kind']=='GATE'},
            'extra/missing gate node')
    return spec['gates']


def actual(waves,config,result,events):
    from .scaleout_verify import event_time
    gates=[(wave,gate) for wave in waves for gate in structure(wave,config)]
    report=result['service'].get('phase_completion')
    if not enabled(config):
        require(report is None,'undeclared native phase completion')
        return
    require(report and report['mode']==MODE,'missing effective phase completion mode')
    rows={(r['graph_id'],r['id']):r for r in report['gates']}
    require(len(rows)==len(report['gates'])==len(gates),'missing/duplicate native phase gate')
    for wave,gate in gates:
        key=wave['graph_id'],gate['id'];require(key in rows,'missing native phase identity')
        row=rows[key];arrivals=[]
        for m in gate['members']:
            begin=event_time(events,wave['graph_id'],m['rank'],m['node'],'issue');arrivals.append(begin)
            require(dict(m,arrived_ns=begin) in row['members'],'native gate arrival differs from actual ASTRA issue')
        finish=max(arrivals)
        require(row['released_ns']==finish and len(row['members'])==len(gate['members']),'gate released before last member')
        for m in gate['members']:
            require(event_time(events,wave['graph_id'],m['rank'],m['node'],'callback')==finish,'gate callback differs from group completion')
