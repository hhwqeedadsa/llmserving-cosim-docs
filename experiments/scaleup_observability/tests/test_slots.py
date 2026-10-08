"""Independent input guards and hand-computed occupancy checks for slot comparisons."""
from pathlib import Path
import shutil
import tempfile
import unittest

import yaml

from experiments.scaleup_observability.common import read, save
from experiments.scaleup_observability.inputs import prepare, verified_case
from experiments.scaleup_observability.slots import set_slots, verify_pair_inputs
from experiments.scaleup_observability.slot_analysis import occupancy_intervals, wait_rows


class PairedInputs(unittest.TestCase):
    def test_only_slot_setting_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepare(root/'normal')
            _,base=verified_case(root/'normal','s0-b8')
            target=root/'two';shutil.copytree(base,target)
            save(target/'layers.json',set_slots(read(base/'layers.json'),2))
            scenario=yaml.safe_load((base/'scenario.yaml').read_text())
            (target/'scenario.yaml').write_text(yaml.safe_dump(set_slots(scenario,2)))
            verify_pair_inputs(base,target)
            service=read(target/'service.json');service['requests'][0]['arrival_ns']=1
            save(target/'service.json',service)
            with self.assertRaisesRegex(ValueError,'service.json'):
                verify_pair_inputs(base,target)

    def test_worker_change_is_not_a_slot_contrast(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepare(root/'normal')
            _,base=verified_case(root/'normal','s2-b8')
            target=root/'two';shutil.copytree(base,target)
            save(target/'layers.json',set_slots(read(base/'layers.json'),2))
            scenario=set_slots(yaml.safe_load((base/'scenario.yaml').read_text()),2)
            scenario['backend']['attributes']['ns3::UbLdstInstance::ThreadNum']=2
            (target/'scenario.yaml').write_text(yaml.safe_dump(scenario))
            with self.assertRaisesRegex(ValueError,'Scenario differs beyond'):
                verify_pair_inputs(base,target)


def send(identity, ready, start, end, rank=0):
    return dict(rank_id=rank,node_id=identity,kind='SEND',layer_id=1,
                dependency_ready_ns=ready,start_ns=start,end_ns=end)


class Occupancy(unittest.TestCase):
    def test_full_and_free_wait_with_release_boundary(self):
        nodes=[send(1,0,2,6),send(2,0,6,9),send(3,3,9,12)]
        intervals=occupancy_intervals(nodes,1,12)
        rows=wait_rows(nodes,intervals)
        self.assertEqual([(r['wait_ns'],r['full_slot_ns'],r['free_slot_ns']) for r in rows],
                         [(2,0,2),(6,4,2),(6,6,0)])
        self.assertEqual(sum((i['end_ns']-i['start_ns'])*i['active_sends'] for i in intervals),10)

    def test_two_slots_partial_occupancy_and_zero_time_local(self):
        nodes=[send(1,0,0,10),send(2,0,4,8),send(3,0,5,5),send(4,0,10,12)]
        intervals=occupancy_intervals(nodes,2,12)
        rows=wait_rows(nodes,intervals)
        self.assertEqual(max(i['active_sends'] for i in intervals),2)
        self.assertEqual((rows[-1]['full_slot_ns'],rows[-1]['free_slot_ns'],rows[-1]['zero_active_ns']),(4,6,0))
        self.assertEqual(sum((i['end_ns']-i['start_ns'])*i['active_sends'] for i in intervals),16)

    def test_rank_isolation_and_over_limit_rejection(self):
        nodes=[send(1,0,0,10),send(2,0,0,10,rank=1),send(3,0,10,12,rank=1)]
        rows=wait_rows(nodes,occupancy_intervals(nodes,1,12))
        self.assertEqual(rows[-1]['full_slot_ns'],10)
        with self.assertRaisesRegex(ValueError,'exceeds'):
            occupancy_intervals([send(1,0,0,10),send(2,0,5,8)],1,10)


if __name__=='__main__':unittest.main()
