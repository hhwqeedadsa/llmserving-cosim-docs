"""Observation boundaries do not substitute trace length for a real request target."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.evidence_service import EvidenceService

ROOT=Path(__file__).resolve().parents[2]


def fixture():
    return (json.loads((ROOT/'fixtures/m9_evidence/service.json').read_text()),
            json.loads((ROOT/'fixtures/m8_composed_service/layers.json').read_text()))


def drive(service,path):
    (path/'graphs').mkdir();now=0
    while True:
        finished=sorted(g for g,w in service.waves.items() if service.history[w['history_index']]['submitted_ns']+10000==now)
        completed=[dict(kind='WAVE_DONE',graph_id=service.waves[g]['graph_id'],wave_id=service.waves[g]['wave_id'],finished_ns=now) for g in finished]
        idle=[r for r in service.ranks if service.owners[r] not in service.waves or service.owners[r] in finished]
        response=service.decide(dict(schema_version='cosim/1',type='DECIDE',run_id=service.config['run_id'],
            decision_id=service.decision_id,now_ns=now,completed=completed,available_rank_ids=idle),path)
        if response['service_finished']:return
        future=[r.arrival for r in service.future]
        future.extend(service.history[w['history_index']]['submitted_ns']+10000 for w in service.waves.values())
        now=min(future)


class EvidenceServiceTest(unittest.TestCase):
    def test_unknown_targets_retire_without_native_completion(self):
        c,l=fixture();s=EvidenceService(c,l)
        self.assertTrue(all(r.output==float('inf') for r in s.requests))
        with tempfile.TemporaryDirectory() as tmp:drive(s,Path(tmp))
        self.assertEqual(s.completed_ids,set());self.assertEqual(s.retired_ids,{201,202,203,204,401,402,404})
        self.assertEqual(len(s.consumed),36)
        self.assertTrue(all(r['target_tokens'] is None and r['status']=='OBSERVATION_ENDED' for r in s.snapshot()))
        self.assertTrue(all(not q.done and not q.running and not q.inflight for q in s.schedulers))
        self.assertTrue(all(not r['request_blocks'] and r['kv_used_bytes']==0 for r in s.memory_snapshot()))
        self.assertEqual(sum(e['event']=='observation_cleanup' for e in s.memory_events),7)
        self.assertFalse(any(e['event']=='release' for e in s.memory_events))
        self.assertEqual([(r.id,r.num_computed_tokens,r.num_tokens_reached) for r in s.requests if r.id==203],[(203,10,11)])
        self.assertTrue(all(r.end_time==-1 and r.latency==-1 for r in s.requests))

    def test_known_target_preserved_and_completion_is_separate(self):
        c,l=fixture();c['requests'][0]['target_total_tokens']=100;c['requests'][1]['target_total_tokens']=7
        s=EvidenceService(c,l)
        with tempfile.TemporaryDirectory() as tmp:drive(s,Path(tmp))
        rows={r['request_id']:r for r in s.snapshot()}
        self.assertEqual(rows[201]['target_tokens'],100);self.assertEqual(rows[201]['num_tokens_reached'],6)
        self.assertEqual(rows[201]['completion_reason'],'observation_window_end')
        self.assertEqual(rows[202]['completion_reason'],'declared_target_reached')
        self.assertEqual(s.completed_ids,{202});self.assertNotIn(202,s.retired_ids)
        self.assertEqual([r.id for q in s.schedulers for r in q.done],[202])

    def test_capacity_wait_and_refill_use_window_bound_only(self):
        c,l=fixture();c['service']['groups'][0]['memory']['ranks'][0]['device_bytes']-=8192
        c['requests'][0]['target_total_tokens']=100
        s=EvidenceService(c,l)
        with tempfile.TemporaryDirectory() as tmp:drive(s,Path(tmp))
        self.assertEqual([[t['request_id'] for t in w['tokens']] for w in s.history if w['group_id']=='ep0'],
            [['201','202','204'],['201','204'],['201'],['203'],['203']])
        self.assertTrue(any(e.get('reason')=='kv_capacity' for e in s.memory_events))
        self.assertEqual(s.requirements[201]['reserved_kv_tokens'],5)
        self.assertEqual(s.completed_ids,set())

    def test_invalid_source_selection_and_profile_hash(self):
        mutations=[lambda c:c['route_manifest'].update(sha256='0'*64),
            lambda c:c['requests'][0].update(decode_steps=4),lambda c:c['requests'][0].update(first_decode_entry=0),
            lambda c:c['requests'][0].update(target_total_tokens=5),lambda c:c['requests'][1].update(file_id='201'),
            lambda c:c['service']['groups'][0]['model_spec'].update(name='unrelated-model')]
        for mutate in mutations:
            c,l=fixture();mutate(c)
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):EvidenceService(c,l)


if __name__=='__main__':unittest.main()
