"""Lossless gzip research tables and explicitly encoded zero port windows.

The observed reader touches only its supplied observed directory. Truth and
global execution records remain separate, including clock transformations.
"""
import gzip
import json
from pathlib import Path
import re
import zlib

from .observation import PORT_FIELDS,validate_public_config,validate_endpoint_rows,windows
from .plan import integer,require
from .record_storage import iter_file


class ResearchWriter:
    def __init__(self,root,max_bytes,max_record_bytes):
        integer(max_bytes,'research compressed budget',1)
        integer(max_record_bytes,'research record budget',1,64*2**20)
        self.root=Path(root);self.limit=max_bytes;self.max_record_bytes=max_record_bytes;self.used=0;self.streams={}
        self.root.mkdir(parents=True,exist_ok=False)

    def open(self,scope,name):
        require(scope in ('observed','truth','metrics') and re.fullmatch(r'[a-z_-]+\.(jsonl|csv)',name),
                'Invalid research table name')
        key=scope,name;require(key not in self.streams,'Repeated research table')
        directory=self.root/scope/'records';directory.mkdir(parents=True,exist_ok=True)
        filename=name+'.gz';owner=self;raw=(directory/filename).open('xb')
        class BudgetFile:
            def write(self,data):
                require(len(data)<=owner.limit-owner.used,'Research compressed disk budget exceeded')
                written=raw.write(data);owner.used+=written;return written
            def flush(self):raw.flush()
        try:compressed=gzip.GzipFile(filename='',fileobj=BudgetFile(),mode='wb',mtime=0,compresslevel=1)
        except BaseException:
            raw.close();raise
        self.streams[key]=dict(raw=raw,stream=compressed,ref=dict(name=filename,records=0,raw_bytes=0,compressed_bytes=0,crc32=0))

    def add(self,scope,name,row):
        data=(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n').encode()
        require(len(data)<=self.max_record_bytes,'Research record size budget exceeded')
        item=self.streams[scope,name];item['stream'].write(data);ref=item['ref']
        ref['records']+=1;ref['raw_bytes']+=len(data);ref['crc32']=zlib.crc32(data,ref['crc32'])

    def finish(self,logical_windows):
        scopes={}
        for (scope,name),item in self.streams.items():
            item['stream'].close();item['raw'].close();ref=item['ref']
            ref['compressed_bytes']=(self.root/scope/'records'/ref['name']).stat().st_size
            scopes.setdefault(scope,{})[name]=ref
        for scope,refs in scopes.items():
            value=dict(schema_version='moe-ub-research-storage/3' if scope=='observed' and 'endpoint_send_state.jsonl' in refs else 'moe-ub-research-storage/2' if scope=='observed' and 'queue_windows.csv' in refs else 'moe-ub-research-storage/1',
                       max_record_bytes=self.max_record_bytes,fields=refs)
            if scope=='observed':value.update(window_encoding='nonzero_with_declared_zero_windows',logical_windows=logical_windows)
            (self.root/scope/'storage.json').write_text(json.dumps(value,indent=2)+'\n')

    def close(self):
        # On failure close descriptors without emitting a successful manifest.
        for item in self.streams.values():
            try:item['stream'].close()
            except Exception:pass
            item['raw'].close()


def manifest(directory):
    directory=Path(directory)
    require(directory.is_dir() and not directory.is_symlink(),'Research scope must be a real directory')
    path=directory/'storage.json'
    require(path.is_file() and not path.is_symlink(),'Missing or symlinked research storage manifest')
    require(path.stat().st_size<=2**20,'Research storage manifest too large')
    value=json.loads(path.read_text())
    require(value['schema_version'] in ('moe-ub-research-storage/1','moe-ub-research-storage/2','moe-ub-research-storage/3'),'Unknown research storage')
    integer(value['max_record_bytes'],'research line limit',1,64*2**20)
    require(isinstance(value['fields'],dict) and not (directory/'records').is_symlink(),'Invalid research file references')
    return value


def stored_rows(directory,name):
    value=manifest(directory);ref=value['fields'][name]
    path=Path(directory)/'records'/ref['name']
    require(not path.is_symlink(),'Research record symlinks are forbidden')
    yield from iter_file(directory,ref,value['max_record_bytes'])


def sparse_windows(directory,public):
    """Validate physical nonzero rows, without walking billions of zero windows."""
    _,ports,total=validate_public_config(public);meta=manifest(directory)
    require(set(meta)=={'schema_version','max_record_bytes','fields','window_encoding','logical_windows'} and
        meta['window_encoding']=='nonzero_with_declared_zero_windows' and
        type(meta['logical_windows']) is int and meta['logical_windows']==total,'Invalid complete window declaration')
    order={(p['switch_id'],p['port_id']):(i,p) for i,p in enumerate(ports)};previous=(-1,-1)
    for row in stored_rows(directory,'port_windows.csv'):
        require(set(row)==set(PORT_FIELDS),'Invalid compressed port fields')
        for name in ('switch_id','port_id'):integer(row[name],name,0,2**32-1)
        key=row['switch_id'],row['port_id'];require(key in order and row['direction']=='egress','Unknown compressed port')
        ordinal,port=order[key];left=row['local_window_start_ns'];right=row['local_window_end_ns']
        integer(left,'window start',port['start_local_ns'],port['end_local_ns']-1)
        index=(left-port['start_local_ns'])//port['period_ns']
        require(left==port['start_local_ns']+index*port['period_ns'] and type(right) is int and
            right==min(left+port['period_ns'],port['end_local_ns']) and (ordinal,index)>previous,
            'Unordered, repeated or misaligned compressed window')
        previous=ordinal,index
        integer(row['counted_packets'],'counted_packets',1,2**64-1)
        integer(row['counted_packet_bytes'],'counted_packet_bytes',row['counted_packets'],2**64-1)
        yield row


def logical_windows(directory):
    public=json.loads((Path(directory)/'public_config.json').read_text())
    _,ports,_=validate_public_config(public);source=iter(sparse_windows(directory,public));pending=next(source,None)
    for port in ports:
        for left,right in windows(port):
            row=dict(switch_id=port['switch_id'],port_id=port['port_id'],direction='egress',
                     local_window_start_ns=left,local_window_end_ns=right,counted_packets=0,counted_packet_bytes=0)
            if pending is not None and all(row[k]==pending[k] for k in PORT_FIELDS[:5]):
                row=pending;pending=next(source,None)
            yield row
    require(pending is None,'Unmatched compressed window')


def validate_observed_storage(directory):
    directory=Path(directory);meta=manifest(directory)
    require({p.name for p in directory.iterdir()}=={'public_config.json','storage.json','records'},
            'Missing or unexpected compressed observed file')
    send_state=meta['schema_version']=='moe-ub-research-storage/3'
    statistics=meta['schema_version'] in ('moe-ub-research-storage/2','moe-ub-research-storage/3')
    require(set(meta['fields'])=={'endpoint_ops.jsonl','port_windows.csv'}|
            ({'queue_windows.csv','endpoint_stages.jsonl'} if statistics else set())|({'endpoint_send_state.jsonl'} if send_state else set()),'Observed table whitelist mismatch')
    require({p.name for p in (directory/'records').iterdir()}=={r['name'] for r in meta['fields'].values()},
            'Unexpected observed record file')
    public_path=directory/'public_config.json'
    require(public_path.is_file() and not public_path.is_symlink(),'Invalid public config file')
    public=json.loads(public_path.read_text());ranks,ports,total=validate_public_config(public)
    require(statistics==(public['schema_version'] in ('moe-ub-observed/3','moe-ub-observed/4')) and
            send_state==(public['schema_version']=='moe-ub-observed/4'),'Public statistic/storage mismatch')
    count=sum(1 for _ in sparse_windows(directory,public))
    endpoints=validate_endpoint_rows(stored_rows(directory,'endpoint_ops.jsonl'),ranks)
    if statistics:
        from .operation_stages import validate_stages
        validate_stages(stored_rows(directory,'endpoint_stages.jsonl'),stored_rows(directory,'endpoint_ops.jsonl'))
        sum(1 for _ in sparse_queue_windows(directory,public))
    if send_state:
        from .send_state import checked_rows
        sum(1 for _ in checked_rows(stored_rows(directory,'endpoint_send_state.jsonl'),public['send_state'],ranks))
    return dict(schema_version='moe-ub-observed-validation/1',passed=True,endpoint_records=endpoints,
                port_windows=total,zero_windows=total-count,access_scope='observed_only')


def sparse_queue_windows(directory,public):
    from .queue_statistics import QUEUE_FIELDS,OBJECTS,check_values
    _,ports,_=validate_public_config(public)
    require(public['schema_version'] in ('moe-ub-observed/3','moe-ub-observed/4'),'Queue windows were not captured')
    order={(p['switch_id'],p['port_id']):(i,p) for i,p in enumerate(ports)};previous=(-1,-1)
    for row in stored_rows(directory,'queue_windows.csv'):
        require(set(row)==set(QUEUE_FIELDS) and row['direction']=='egress','Invalid queue observation fields')
        for name in ('switch_id','port_id'):integer(row[name],name,0,2**32-1)
        key=row['switch_id'],row['port_id'];require(key in order,'Unknown observed queue port')
        ordinal,port=order[key];left,right=row['local_window_start_ns'],row['local_window_end_ns']
        integer(left,'queue window start',port['start_local_ns'],port['end_local_ns']-1)
        index=(left-port['start_local_ns'])//port['period_ns']
        require(left==port['start_local_ns']+index*port['period_ns'] and type(right) is int and
                right==min(left+port['period_ns'],port['end_local_ns']) and (ordinal,index)>previous,'Invalid queue window order/bounds')
        previous=ordinal,index;check_values(row,right-left)
        require(any(row[k+'_peak_bytes'] for k in OBJECTS),'Empty queue window stored as nonzero')
        yield row


def logical_queue_windows(directory):
    """Expand declared queue windows; packet silence alone never means no queue."""
    from .queue_statistics import OBJECTS
    public=json.loads((Path(directory)/'public_config.json').read_text());_,ports,_=validate_public_config(public)
    source=iter(sparse_queue_windows(directory,public));pending=next(source,None)
    for port in ports:
        for left,right in windows(port):
            row=dict(switch_id=port['switch_id'],port_id=port['port_id'],direction='egress',local_window_start_ns=left,local_window_end_ns=right,
                     **{k+suffix:0 for k in OBJECTS for suffix in ('_byte_ns','_mean_bytes','_peak_bytes')})
            if pending is not None and all(row[k]==pending[k] for k in PORT_FIELDS[:5]):row=pending;pending=next(source,None)
            yield row
    require(pending is None,'Unmatched queue window')
