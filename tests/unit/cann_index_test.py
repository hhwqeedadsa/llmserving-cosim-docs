"""Whole-plan equality to pre-indexing plans; independent CANN tests stay active."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from moe_ub_cosim.plan import build_plan, validate_plan

ROOT = Path(__file__).resolve().parents[2]


def digest(plan):
    # Keep list and object insertion order: ordering regressions must be visible.
    return hashlib.sha256(json.dumps(plan,separators=(',',':')).encode()).hexdigest()


def cases():
    base=json.loads((ROOT/'fixtures/m10_cann/layer.json').read_text())
    for world in (2,3,5):
        ranks=[2,7,12,21,35][:world]
        for layout in ('undeclared','separate','pairs','one-chip'):
            for empty in (False,True):
                label=f'ep{world}-{layout}-'+('single' if empty else 'uneven')
                c=deepcopy(base);c.update(rank_ids=ranks,rank_to_ub_node=[101+11*i for i in range(world)],
                    expert_placement=[r for r in ranks for _ in range(3)],top_k=min(world+1,5),
                    hidden_dim=1025 if world==3 else 1024,chunk_bytes=257 if world==3 else 8192,
                    send_slots_per_rank=1 if world==2 else 2,
                    batch_limits=dict(max_num_seqs=4,max_num_batched_tokens=4),tokens=[])
                c['cann_profile']['aiv_lanes']={'undeclared':1,'separate':2,'pairs':3,'one-chip':48}[layout]
                if layout=='undeclared':c.pop('deployment')
                else:
                    c['deployment']['ranks']=[dict(rank_id=r,die_id=f'die-{i}',server_id='server-0',
                        chip_id=f"chip-{0 if layout=='one-chip' else i//2 if layout=='pairs' else i}")
                        for i,r in enumerate(ranks)]
                    if layout=='one-chip':c['deployment']['local_prepare_ns_per_vector']=dict(dispatch=0,combine=0)
                if empty:
                    c['tokens']=[dict(request_id=label,src_rank=ranks[0],phase='decode',
                        token_index=0,layer_id=c['layer_id'],expert_ids=list(range(c['top_k'])))]
                else:
                    # Interleave requests; one source rank deliberately has no Token.
                    for slot in range(3):
                        for i,r in reversed(list(enumerate(ranks))):
                            if i==1 or slot>i%3:continue
                            experts=[(i*3+slot+2*k)%(3*world) for k in range(c['top_k'])]
                            # Odd stride when even expert cardinality would wrap early.
                            if len(set(experts))!=len(experts):
                                experts=[(i*3+slot+k)%(3*world) for k in range(c['top_k'])]
                            c['tokens'].append(dict(request_id=f'{label}-{r}-{slot}',src_rank=r,phase='decode',
                                token_index=slot*7+i,layer_id=c['layer_id'],expert_ids=experts))
                yield label,c


class CannIndexTest(unittest.TestCase):
    def test_whole_empty_graph_still_rejected(self):
        _,config=next(cases());config['tokens']=[]
        with self.assertRaisesRegex(ValueError,'Token count outside'):build_plan(config)

    def test_all_plan_fields_and_order_match_pre_indexing_baseline(self):
        expected=json.loads((ROOT/'fixtures/m10_cann_indexing/golden.json').read_text())['cases']
        actual={}
        for label,c in cases():
            with self.subTest(label=label):
                original=deepcopy(c);plan=build_plan(c)
                self.assertEqual(c,original,'Indexing must not mutate the input')
                actual[label]=digest(plan)
                self.assertEqual(actual[label],expected[label])
        self.assertEqual(set(actual),set(expected))

    def test_indexed_counts_and_sources_remain_checked(self):
        _,config=next(cases());plan=build_plan(config)
        for field in ('token_count','sources','deps'):
            bad=deepcopy(plan)
            if field=='token_count':
                t=next(t for t in bad['transfers'] if 'token_count' in t);t[field]+=1
            elif field=='sources':
                t=next(t for t in bad['transfers'] if t['sources']);t[field][0]['expert_ids']=[999]
            else:
                n=next(n for g in bad['rank_graphs'] for n in g['nodes'] if n['deps']);n[field]=[]
            with self.subTest(field=field),self.assertRaises(ValueError):validate_plan(bad)


if __name__=='__main__':unittest.main()
