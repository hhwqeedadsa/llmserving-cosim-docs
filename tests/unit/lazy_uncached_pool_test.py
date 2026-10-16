"""Compare sparse and upstream eager allocators through public pool operations."""
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'third_party/LLMServingSim'))
from serving.core.block_pool import BlockPool, Device
from moe_ub_cosim.lazy_uncached_pool import LazyUncachedBlockPool


class LazyPoolTest(unittest.TestCase):
    def test_fifo_accounting_sharing_and_reset_match_pinned_pool(self):
        pools=[kind(Device.NPU,31,16,4096,enable_caching=False)
               for kind in (BlockPool,LazyUncachedBlockPool)]
        held=[{},{}];rng=random.Random(17)
        for step in range(300):
            free=pools[0].get_num_free_blocks()
            if free and (not held[0] or rng.random()<0.65):
                count=rng.randint(1,min(free,5))
                allocated=[p.get_new_blocks(count) for p in pools]
                self.assertEqual([b.block_id for b in allocated[0]],[b.block_id for b in allocated[1]])
                for state,blocks in zip(held,allocated):
                    state[step]=blocks
                if rng.random()<0.5:
                    for pool,blocks in zip(pools,allocated):
                        pool.touch(blocks);pool.free_blocks(blocks)
            elif held[0]:
                key=rng.choice(list(held[0]))
                for pool,state in zip(pools,held):pool.free_blocks(reversed(state.pop(key)))
            self.assertEqual(pools[0].get_num_free_blocks(),pools[1].get_num_free_blocks())
            self.assertEqual(pools[0].used_bytes(),pools[1].used_bytes())
            with self.assertRaises(RuntimeError):pools[1].get_new_blocks(free+32)
        for pool in pools:
            pool.reset()
            self.assertTrue(pool.is_free())
        self.assertEqual([b.block_id for b in pools[0].get_new_blocks(31)],
                         [b.block_id for b in pools[1].get_new_blocks(31)])

    def test_large_declared_capacity_does_not_allocate_idle_metadata(self):
        pool=LazyUncachedBlockPool(Device.NPU,1000000,16,4096)
        self.assertEqual(len(pool.blocks.materialized),0)
        blocks=pool.get_new_blocks(129)
        self.assertEqual(len(pool.blocks.materialized),129)
        self.assertEqual(pool.get_num_free_blocks(),1000000-129)
        pool.free_blocks(reversed(blocks))
        self.assertTrue(pool.is_free())
        with self.assertRaises(ValueError):
            LazyUncachedBlockPool(Device.NPU,10,16,4096,enable_caching=True)


if __name__=='__main__':unittest.main()
