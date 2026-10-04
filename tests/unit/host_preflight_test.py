"""Independent contribution arithmetic must agree with the actual generated DAG."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.host_preflight import add_token,empty,usage
from moe_ub_cosim.plan import build_plan
from moe_ub_cosim.resources import actual_usage


class HostPreflightTest(unittest.TestCase):
    def test_three_localities_status_and_chunk_counts(self):
        root=Path(__file__).resolve().parents[2]
        base=json.loads((root/'fixtures/m10_full_model/layers.json').read_text())['groups'][0]['layers'][0]
        for chunk,far,total,nodes in [(1024,13,20,55),(128,34,41,97)]:
            c=deepcopy(base);c['chunk_bytes']=chunk
            c['tokens']=[dict(request_id=str(i),src_rank=src,phase='decode',token_index=2,layer_id=1,expert_ids=experts)
                for i,(src,experts) in enumerate([(2,[0,2]),(12,[0,5])])]
            chips={r['rank_id']:r['chip_id'] for r in c['deployment']['ranks']};positions={r:i for i,r in enumerate(c['rank_ids'])}
            row=empty()
            for t in c['tokens']:add_token(row,t['src_rank'],t['expert_ids'],c['expert_placement'],chips,positions)
            counted=usage(c,row);plan=build_plan(c)
            self.assertEqual((counted['same_rank_contributions'],counted['same_chip_contributions'],counted['cross_chip_contributions']),(2,1,1))
            self.assertEqual({k:counted[k] for k in ('transfers','fabric_transfers','local_transfers','nodes')},
                dict(transfers=total,fabric_transfers=far,local_transfers=7,nodes=nodes))
            self.assertEqual(actual_usage(plan),{k:counted[k] for k in actual_usage(plan)})
            self.assertEqual(counted['fabric_business_bytes'],3072);self.assertEqual(counted['fabric_payload_bytes'],3404)
            self.assertEqual(sum(t['payload_bytes'] for t in plan['transfers'] if 'ub_task_id' in t),3404)


if __name__=='__main__':unittest.main()
