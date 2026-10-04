"""Finite disk-backed transfer lookup and independent target-write accounting.

Only numeric packet-validation fields are indexed. Source JSON is streamed from
the original compressed records; it is never duplicated in an SQLite/JSON index.
"""
from bisect import bisect_right
from array import array
from collections import OrderedDict
from pathlib import Path
import mmap
import struct

from .plan import integer,require

ROW=struct.Struct('<QIIIIQ')  # transfer, task, operation, payload, region layout, delivered


class TransferIndex:
    def __init__(self,path,*,max_bytes,max_operations,max_layouts,max_id_ranges,cache_rows=8192):
        self.path=Path(path)
        self.limit=max_bytes;self.max_operations=max_operations;self.max_layouts=max_layouts
        self.max_id_ranges=max_id_ranges;self.cache_limit=cache_rows
        for value in (max_bytes,max_operations,max_layouts,max_id_ranges,cache_rows):integer(value,'index limit',1)
        self.stream=self.path.open('x+b',buffering=1024*1024)
        self.operations=[];self.operation_index={};self.layouts=[];self.layout_index={}
        self.ranges=[];self.task_starts=[];self.count=0;self.last_transfer=-1;self.last_task=-1
        # Transfer IDs include local deliveries, so retain exact fabric-only
        # interval starts/row offsets (16 bytes per interval). Packet lookup then
        # performs one mapped read, not ~28 mapped reads at the target scale.
        self.transfer_starts=array('Q');self.transfer_rows=array('Q')
        self.cache=OrderedDict();self.sealed=False;self.bytes_written=0;self.mapping=None

    def add(self,transfer):
        require(not self.sealed,'Cannot append to sealed transfer index')
        tid=integer(transfer['transfer_id'],'transfer id',1,2**64-1)
        task=integer(transfer['ub_task_id'],'task id',0,2**32-1)
        payload=integer(transfer['payload_bytes'],'payload bytes',1,2**32-1)
        require(tid>self.last_transfer and task>self.last_task,'Duplicate or unordered transfer/task identity')
        op=transfer['operation_id'];require(isinstance(op,str) and op,'Invalid indexed operation')
        if op not in self.operation_index:
            require(len(self.operations)<self.max_operations,'Transfer index operation budget exceeded')
            self.operation_index[op]=len(self.operations);self.operations.append(op)
        regions=transfer.get('payload_regions',[dict(begin=0,end=payload,kind='business')])
        layout=tuple((r['begin'],r['end'],r['kind']) for r in regions)
        previous=0
        for a,b,kind in layout:
            integer(a,'region begin',previous,previous);integer(b,'region end',a+1,payload)
            require(kind in ('business','metadata','padding'),'Invalid payload region kind');previous=b
        require(previous==payload,'Incomplete indexed payload regions')
        if layout not in self.layout_index:
            require(len(self.layouts)<self.max_layouts,'Transfer index layout budget exceeded')
            self.layout_index[layout]=len(self.layouts);self.layouts.append(layout)
        if task!=self.last_task+1 or not self.ranges:
            require(len(self.ranges)<self.max_id_ranges,'Transfer index identity-range budget exceeded')
            self.ranges.append([task,task,self.count]);self.task_starts.append(task)
        else:self.ranges[-1][1]=task
        if tid!=self.last_transfer+1 or not self.transfer_starts:
            require(len(self.transfer_starts)<self.max_id_ranges,'Transfer identity-range budget exceeded')
            self.transfer_starts.append(tid);self.transfer_rows.append(self.count)
        require((self.count+1)*ROW.size<=self.limit,'Transfer index disk budget exceeded')
        self.stream.write(ROW.pack(tid,task,self.operation_index[op],payload,self.layout_index[layout],0))
        self.last_transfer=tid;self.last_task=task;self.count+=1;self.bytes_written+=ROW.size

    def seal(self):
        require(not self.sealed,'Transfer index already sealed')
        self.stream.flush()
        if self.count:self.mapping=mmap.mmap(self.stream.fileno(),0,access=mmap.ACCESS_WRITE)
        self.sealed=True

    def _row(self,index):
        require(0<=index<self.count,'Transfer index row outside file')
        require(self.mapping is not None,'Unmapped transfer index')
        return ROW.unpack_from(self.mapping,index*ROW.size)

    def _task_row(self,task):
        require(self.sealed,'Unsealed transfer index')
        integer(task,'target write task',0,2**32-1)
        position=bisect_right(self.task_starts,task)-1
        require(position>=0,'Unknown target write task')
        first,last,index=self.ranges[position]
        require(task<=last,'Unknown target write task')
        return index+task-first

    def delivered(self,task):return self._row(self._task_row(task))[-1]

    def add_write(self,task,size):
        index=self._task_row(task);row=self._row(index)
        integer(size,'target write bytes',1,row[3]);total=row[-1]+size
        require(total<=row[3],'Duplicate or oversized target write')
        struct.pack_into('<Q',self.mapping,index*ROW.size+ROW.size-8,total)

    def get(self,tid):
        require(self.sealed,'Unsealed transfer index')
        integer(tid,'packet transfer id',1,2**64-1)
        if tid in self.cache:self.cache.move_to_end(tid);return self.cache[tid]
        position=bisect_right(self.transfer_starts,tid)-1
        if position<0:return None
        ordinal=self.transfer_rows[position]+tid-self.transfer_starts[position]
        if ordinal>=self.count:return None
        row=self._row(ordinal)
        if row[0]!=tid:return None
        result=dict(transfer_id=row[0],ub_task_id=row[1],operation_id=self.operations[row[2]],payload_bytes=row[3],
                    payload_regions=[dict(begin=a,end=b,kind=k) for a,b,k in self.layouts[row[4]]])
        if len(self.cache)==self.cache_limit:self.cache.popitem(last=False)
        self.cache[tid]=result
        return result

    def close(self):
        if self.mapping is not None:self.mapping.close();self.mapping=None
        self.stream.close()

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
