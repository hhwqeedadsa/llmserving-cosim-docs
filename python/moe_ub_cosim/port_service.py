"""Bounded, directed physical-port service intervals; no task identity inputs."""
from .plan import integer, require


def validate(value, topology, execution_end_ns=None):
    require(isinstance(value,dict) and set(value)=={'schema_version','execution_end_ns','intervals'} and
            value['schema_version']=='moe-ub-port-service/1', 'Invalid port service schema/fields')
    bound=integer(value['execution_end_ns'],'port service execution_end_ns',1)
    require(execution_end_ns is None or bound==execution_end_ns,'Port service execution budget differs from run')
    rows=value['intervals']
    require(isinstance(rows,list) and len(rows)<=4096,'Port service interval budget is 4096')
    connected={(link[side],link[side+'_port']) for link in topology['links'] for side in ('src','dst')}
    previous={}
    for row in rows:
        require(isinstance(row,dict) and set(row)=={'node_id','port_id','start_ns','end_ns','rate_bps'},
                'Invalid port service interval fields')
        for key in ('node_id','port_id'):integer(row[key],key,0,2**32-1)
        key=row['node_id'],row['port_id']
        require(key in connected,'Port service endpoint is not connected')
        begin=integer(row['start_ns'],'start_ns',0,bound)
        end=integer(row['end_ns'],'end_ns',1,bound)
        integer(row['rate_bps'],'rate_bps',1,2**64-1)
        require(begin<end and begin>=previous.get(key,0),'Port service intervals must be ordered, nonoverlapping and nonempty')
        previous[key]=end
    return value
