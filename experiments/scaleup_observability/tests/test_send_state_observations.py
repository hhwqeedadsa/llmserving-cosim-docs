"""Hand-computed sample loss, independent evaluation and public-only inputs."""
from collections import defaultdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from experiments.scaleup_observability.common import digest, read, save
from experiments.scaleup_observability.inputs import make_input, verified_case
from experiments.scaleup_observability.observation_evaluate import accumulate as baseline_accumulate, TOTALS
from experiments.scaleup_observability.send_state_evaluate import accumulate, state_metrics
from experiments.scaleup_observability.send_state_prepare import load_states, snapshot, point_truth
from experiments.scaleup_observability.send_state_spec import (CONDITION, COUNT_FIELDS, QUEUE_FIELDS,
    STATE_FIELDS, SAMPLING, END_NS, GRIDS, classify, grid_id, windows)
from experiments.scaleup_observability.send_state_workflow import prepare_source
from experiments.scaleup_observability.slots import verify_pair_inputs


class SendStateObservations(unittest.TestCase):
    def test_point_classification_does_not_equate_busy_with_pending(self):
        self.assertEqual(classify([0,2,2]),'no_pending')
        self.assertEqual(classify([3,2,2]),'full_pending')
        self.assertEqual(classify([3,1,2]),'other_pending')
        self.assertEqual(classify([0,0,0]),'undetermined')

    def test_sampling_uses_last_same_time_update_without_looking_ahead(self):
        times={r:[0,0,2,4,7,7,9,END_NS] for r in range(8)}
        values={r:[[0,0,0],[0,0,1],[1,1,1],[0,0,1],[2,1,1],[0,1,1],[0,0,1],[0,0,1]] for r in range(8)}
        state=times,values
        self.assertEqual(snapshot(state,0)[0],[0,0,1])
        self.assertEqual(snapshot(state,1)[0],[0,0,1])
        self.assertEqual(snapshot(state,2)[0],[1,1,1])
        self.assertEqual(snapshot(state,7)[0],[0,1,1])
        self.assertEqual(snapshot(state,10)[0],[0,0,1])
        # A complete 2 ns wait occurs between the two samples and is missed.
        ranks={r:([[2,4]],[[2,4]]) for r in range(8)}
        self.assertEqual(point_truth(ranks,0),['no_pending']*8)
        self.assertEqual(point_truth(ranks,2),['full_pending']*8)
        self.assertEqual(point_truth(ranks,4),['no_pending']*8)

    def test_independent_point_and_duration_metrics(self):
        totals=defaultdict(int)
        t=dict(start_ns=0,end_ns=10,active_ns=10,ranks=[[4,0,6]]+[[0,0,10]]*7,
               point_states=['full_pending']+['no_pending']*7)
        p=[dict(selected=True,state='full_pending')]+[dict(selected=False,state='no_pending') for _ in range(7)]
        accumulate(totals,t,p)
        t['point_states']=['no_pending']*8;t['ranks']=[[2,0,8]]+[[0,0,10]]*7
        accumulate(totals,t,[dict(selected=False,state='no_pending') for _ in range(8)])
        result=state_metrics(totals)
        self.assertEqual(result['point_accuracy_when_classified'],1)
        self.assertEqual(result['held_state_agreement_fraction'],152/160)
        self.assertEqual(result['wait_coverage_fraction'],4/6)
        self.assertEqual(result['selected_rank_ns'],10)
        self.assertEqual(result['missed_wait_rank_ns'],2)
        self.assertEqual(result['missed_target_cells'],1)
        self.assertEqual(result['undetermined_fraction'],0)

    def test_baseline_metrics_remain_unchanged(self):
        t=dict(start_ns=0,end_ns=10,active_ns=8,ranks=[[4,1,5]]+[[0,0,10]]*7,
               point_states=['full_pending']+['no_pending']*7)
        old=defaultdict(int);new=defaultdict(int);p=dict(selected=True,state='undetermined')
        baseline_accumulate(old,t,p);accumulate(new,t,[p]*8)
        self.assertEqual({k:old[k] for k in TOTALS},{k:new[k] for k in TOTALS})

    def test_four_frozen_sources_pair_inputs_and_no_old_data(self):
        with tempfile.TemporaryDirectory() as temp:
            batch=Path(temp)/'source';prepare_source(batch)
            self.assertEqual(len(read(batch/'batch.json')['cases']),4)
            for seed,role in ((3,'reference'),(4,'holdout')):
                _,a=verified_case(batch,f's{seed}-b8-slots1');_,b=verified_case(batch,f's{seed}-b8-slots2')
                verify_pair_inputs(a,b)
                self.assertEqual(read(a/'service.json'),make_input(seed,8)[0])
                self.assertEqual(read(a/'observation.json')['schema_version'],'moe-ub-observation-config/4')
            self.assertEqual([c['role'] for c in read(batch/'sources.json')['cases']],['reference','reference','holdout','holdout'])

    def test_uncaptured_input_cannot_be_reconstructed_as_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);save(root/'public_config.json',dict(schema_version='moe-ub-observed/3'))
            with self.assertRaisesRegex(ValueError,'Actual endpoint'):load_states(root)

    def test_isolated_measurement_and_field_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);public=root/'observed';public.mkdir()
            # No raw, source or truth paths are present in the public package.
            with (public/'windows.jsonl').open('w') as stream:
                for period,origin in GRIDS:
                    for a,b in windows(period,origin):
                        stream.write(json.dumps(dict(sample_id='sample-000',grid=grid_id(period,origin),start_ns=a,end_ns=b,
                            ports=[[0]*6 for _ in range(32)],rank_states=[[1,1,1]]+[[0,1,1] for _ in range(7)]))+'\n')
            config=dict(schema_version='scaleup-send-state-public/1',condition=CONDITION,rank_ids=list(range(8)),
                port_ids=[[i,0] for i in range(32)],fields=list(COUNT_FIELDS+QUEUE_FIELDS),state_fields=list(STATE_FIELDS),
                state_sampling=SAMPLING,interval_ns=[0,END_NS],grids=GRIDS,sample_ids=['sample-000'],
                windows_sha256=digest(public/'windows.jsonl'),topology=dict(nodes=[[i,'SWITCH',1,0,0] for i in range(32)]+
                [[i,'DEVICE',1,0,0] for i in range(32,40)],links=[],rank_nodes=[[i,i+32] for i in range(8)]))
            save(public/'manifest.json',config)
            def run(name):
                target=root/name;target.mkdir()
                return subprocess.run([sys.executable,'-m','experiments.scaleup_observability.send_state_measure',str(public),str(target)],capture_output=True,text=True)
            r=run('result');self.assertEqual(r.returncode,0,r.stderr)
            prediction=json.loads((root/'result/predictions.jsonl').read_text().splitlines()[0])
            self.assertTrue(prediction['ranks'][0]['selected']) # no port activity, still a direct local fact
            self.assertTrue(all(r['state']=='no_pending' and not r['selected'] for r in prediction['ranks'][1:]))
            config['true_finish_ns']=123;save(public/'manifest.json',config)
            r=run('bad');self.assertNotEqual(r.returncode,0);self.assertIn('field violation',r.stderr)


if __name__=='__main__':unittest.main()
