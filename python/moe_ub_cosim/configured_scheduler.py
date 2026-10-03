"""Root-owned initialization adapter for the pinned Scheduler and single KV pool.

Native Scheduler.__init__ hardcodes the uniform-expert MemoryModel constructor.
Only initialization is adapted here; request selection, batches, completion and
block allocation/free remain the pinned implementation, without global patches.
"""
from serving.core.block_pool import BlockPool, Device
from serving.core.kv_cache_manager import TieredKVCacheManager
from serving.core.logger import get_logger
from serving.core.memory_model import MemoryModel
from serving.core.scheduler import Scheduler


class ConfiguredMemory(MemoryModel):
    def __init__(self, model, audit, block_size):
        self.model = model['name']
        self.config = model
        self.node_id = self.instance_id = audit['rank_id']
        self.num_npus = self.tp_size = self.pp_size = 1
        self.npu_mem = audit['device_bytes']
        self.cpu_mem = self.cxl_mem = 0
        self.block_size = block_size
        self.enable_prefix_caching = self.enable_prefix_sharing = False
        self.prefix_storage = self.storage_pool = None
        self.lower_pools = []
        self.weight = audit['weight_bytes']
        self._npu_reserved, self._cpu_reserved = audit['non_kv_bytes'], 0
        self._bytes_per_token = self._cluster_bytes_per_token = audit['kv_bytes_per_token']
        self._npu_bytes_per_block = audit['kv_block_bytes']
        self.logger = get_logger(type(self), node_id=self.node_id, instance_id=self.instance_id)
        self.npu_pool = BlockPool(Device.NPU, audit['kv_capacity_blocks'], block_size,
                                  self._npu_bytes_per_block, enable_caching=False,
                                  node_id=self.node_id, instance_id=self.instance_id)
        self.kv = TieredKVCacheManager(block_size, self.npu_pool, enable_caching=False)

    def get_weight(self):
        return self.weight

    def get_kv(self, seq):
        return self._bytes_per_token * seq


class ConfiguredScheduler(Scheduler):
    def __init__(self, model, audit, block_size, token_budget, request_count, max_num_seqs=1):
        self.model, self.config = model['name'], model
        self.node_id = self.instance_id = self.start_npu = audit['rank_id']
        self.max_num_seqs = max_num_seqs
        self.max_num_batched_tokens = token_budget
        self.long_prefill_token_threshold = 0
        self.num_npus = self.tp_size = self.pp_size = 1
        self.req_num, self.pd_type = request_count, 'decode'
        self.enable_prefix_caching = self.enable_prefix_sharing = self.enable_chunked_prefill = False
        self.prefix_storage = None
        self.reserve_full_isl = True
        self.running, self.waiting, self.inflight, self.done = [], [], [], []
        self.batch_ids = -1
        self.recompute_tokens = self.num_preemptions = 0
        self.memory = ConfiguredMemory(model, audit, block_size)
        self.kv = self.memory.kv
        self.logger = get_logger(type(self), node_id=self.node_id, instance_id=self.instance_id)
