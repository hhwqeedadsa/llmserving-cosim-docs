"""Static NIC-scoped remote access registrations over shared physical windows."""
from .scaleout_topology import fields, integer, require
from .transaction_capacity import capacity


def validate_registrations(config, storage, connections):
    enabled='registration_model' in config
    require(enabled == ('registrations' in config), 'registration model/list must appear together')
    if not enabled:return None
    require(config['schema_version'] in ('moe-rdma-transactions/4','moe-rdma-transactions/5') and
            config['registration_model']==dict(mode='nic_scoped_aliases/1'), 'unsupported registration model')
    caps=capacity(config)
    rows=config['registrations']
    require(isinstance(rows,list) and 1<=len(rows)<=caps['registrations'], 'registration count budget exceeded')
    nics={(c['src'],c['source_nic']) for c in connections.values()} | {(c['dst'],c['destination_nic']) for c in connections.values()}
    result={};ids=set()
    for row in rows:
        fields(row,'id owner nic key window_key address bytes allow_atomic','registration')
        for name,low,high in [('id',0,65535),('owner',0,caps['owner']),('nic',0,1024),('key',1,2**32-1),
                              ('window_key',1,2**32-1),('address',0,2**64-1),('bytes',1,64*2**20)]:
            integer(row[name],'registration '+name,low,high)
        require(type(row['allow_atomic']) is bool,'registration permission must be boolean')
        key=row['owner'],row['nic'],row['key']
        require(row['id'] not in ids and key not in result,'repeated registration id or owner/NIC/key')
        require((row['owner'],row['nic']) in nics,'registration NIC must have a connection binding')
        location=row['owner'],row['window_key']
        require(location in storage,'unknown registration storage')
        window,size=storage[location]
        require(window['address']<=row['address'] and row['address']+row['bytes']<=window['address']+size,
                'registration range exceeds storage')
        require(not row['allow_atomic'] or window['allow_atomic'],'registration grants denied atomic permission')
        ids.add(row['id']);result[key]=row
    return result
