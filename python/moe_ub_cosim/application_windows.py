"""Pure local-view admission. No remote-state oracle or provider retirement."""
from .scaleout_topology import fields, integer, require


def validate_application_windows(config, storage, connections):
    enabled = 'application_window_model' in config
    require(enabled == ('application_windows' in config), 'application window model/list must appear together')
    if not enabled:
        require(all('application_uses' not in op and 'release_window' not in op
                    for op in config['operations']), 'application window fields require model')
        return
    require(config['schema_version'] in ('moe-rdma-transactions/4', 'moe-rdma-transactions/5') and
            config['application_window_model'] == dict(mode='local_views/1') and
            'registration_model' in config, 'unsupported application window model/registrations')
    rows = config['application_windows']
    require(isinstance(rows, list) and 1 <= len(rows) <= 32, 'require 1..32 application views')
    views = {}
    for row in rows:
        fields(row, 'id owner window_key address bytes', 'application view')
        for name, lo, hi in [('id', 0, 65535), ('owner', 0, 31), ('window_key', 1, 2**32-1),
                             ('address', 0, 2**64-1), ('bytes', 1, 64*2**20)]:
            integer(row[name], 'application view '+name, lo, hi)
        location = row['owner'], row['window_key']
        require(row['id'] not in views and location in storage, 'duplicate view id/unknown backing storage')
        backing, size = storage[location]
        require(backing['address'] <= row['address'] and row['address']+row['bytes'] <= backing['address']+size,
                'application view exceeds backing storage')
        views[row['id']] = row
    covers = {}; users = {i: set() for i in views}; closed = set()
    for op in config['operations']:
        done = set()
        for d in op['after']:
            done.update(covers[d['operation']])
            if d['event'] == 'completed': done.add(d['operation'])
        covers[op['id']] = done
        owner = op['rank'] if op['kind'] == 'local' else connections[op['connection']]['src']
        ids = op.get('application_uses', [])
        require(isinstance(ids, list) and len(ids) <= 8 and ('application_uses' not in op or ids),
                'application_uses requires 1..8 ids')
        for i in ids: integer(i, 'application use id', 0, 65535)
        require(len(set(ids)) == len(ids) and all(i in views and views[i]['owner'] == owner and
                i not in closed for i in ids), 'application use unknown/foreign/closed/duplicate')
        ranges = [(w['key'], w['address'], 8) for w in op.get('local_wait', [])]
        if 'source' in op:
            s = op['source']; ranges.append((s['key'], s['address'], s['bytes']))
        if 'copy' in op:
            cp = op['copy']; size = cp['source']['bytes']
            ranges.extend((x['key'], x['address'], size) for x in (cp['source'], cp['target']))
        for key, address, size in ranges:
            require(any(views[i]['window_key'] == key and views[i]['address'] <= address and
                        address+size <= views[i]['address']+views[i]['bytes'] for i in ids),
                    'local memory range missing covering application use')
        if 'release_window' in op:
            i = op['release_window']; integer(i, 'release window', 0, 65535)
            require(op['kind'] == 'local' and not any(k in op for k in ('copy', 'local_wait', 'application_uses')),
                    'release_window requires separate pure local node')
            require(i in views and views[i]['owner'] == owner and i not in closed,
                    'application close unknown/foreign/duplicate')
            require(users[i] <= done, 'application close requires completed uses')
            closed.add(i)
        for i in ids: users[i].add(op['id'])
