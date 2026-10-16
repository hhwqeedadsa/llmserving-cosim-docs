"""Sparse metadata for a pinned BlockPool with prefix caching disabled.

Capacity, reference counting and accounting remain the pinned BlockPool's
methods. Only the initially free FIFO is represented by an integer range;
block objects are materialized on first access. Recycled blocks follow that
range, preserving exactly the eager pool's allocation order.
"""
from collections import deque

from serving.core.block_pool import BlockPool, KVCacheBlock, PrefixCacheStats
from serving.core.logger import get_logger


class _Blocks:
    def __init__(self, count):
        self.count=count
        self.materialized={}

    def __len__(self):
        return self.count

    def __getitem__(self, index):
        if not isinstance(index,int) or not 0<=index<self.count:
            raise IndexError(index)
        if index not in self.materialized:
            self.materialized[index]=KVCacheBlock(index)
        return self.materialized[index]


class _FreeQueue:
    def __init__(self, blocks):
        self.blocks=blocks
        self.next_id=0
        self.recycled=deque()
        self.num_free_blocks=len(blocks)

    def popleft_n(self, count):
        if not isinstance(count,int) or not 0<=count<=self.num_free_blocks:
            raise RuntimeError('invalid free-block allocation')
        fresh=min(count,len(self.blocks)-self.next_id)
        result=[self.blocks[i] for i in range(self.next_id,self.next_id+fresh)]
        self.next_id+=fresh
        result.extend(self.recycled.popleft() for _ in range(count-fresh))
        self.num_free_blocks-=count
        return result

    def popleft(self):
        return self.popleft_n(1)[0]

    def append_n(self, blocks):
        self.recycled.extend(blocks)
        self.num_free_blocks+=len(blocks)

    def append(self, block):
        self.append_n([block])

    def remove(self, block):
        # Uncached blocks can be touched after being freed. Such blocks have
        # already been materialized and are necessarily in the recycled FIFO.
        self.recycled.remove(block)
        self.num_free_blocks-=1

    def get_all_free_blocks(self):
        # Explicit diagnostic request may materialize the remaining capacity.
        return [self.blocks[i] for i in range(self.next_id,len(self.blocks))]+list(self.recycled)


class LazyUncachedBlockPool(BlockPool):
    def __init__(self,tier,num_blocks,block_size,bytes_per_block,
                 enable_caching=False,node_id=None,instance_id=None):
        if enable_caching or type(num_blocks) is not int or num_blocks<=0:
            raise ValueError('sparse pool requires positive capacity and disabled caching')
        self.tier,self.num_blocks=tier,num_blocks
        self.block_size,self.bytes_per_block=block_size,bytes_per_block
        self.enable_caching=False
        self.blocks=_Blocks(num_blocks)
        self.free_block_queue=_FreeQueue(self.blocks)
        self.cached_block_hash_to_block={}
        self.stats=PrefixCacheStats()
        self.logger=get_logger(type(self),node_id=node_id,instance_id=instance_id)

    def reset(self):
        self.cached_block_hash_to_block={}
        for block in self.blocks.materialized.values():
            block.ref_cnt=0
            block.reset_hash()
            block.prev_free_block=block.next_free_block=None
        self.free_block_queue=_FreeQueue(self.blocks)
