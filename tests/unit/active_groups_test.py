"""Explicit dormant groups preserve deployment without inventing requests."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from moe_ub_cosim.scaleout_rdma import ROOT
from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import Frontend,make_wave
from moe_ub_cosim.scaleout_verify import verify_replay_requests


def config():
    c=json.loads((ROOT/'examples/scaleout_route_replay/service.json').read_bytes())
    c['parallel']['groups']=[[0,2],[1,3]];c['transport']={}
    c['route_replay']['request_layout']='sparse_active_groups/1'
    c['route_replay']['requests']=[c['route_replay']['requests'][0],c['route_replay']['requests'][2]]
    return c


class ActiveGroups(unittest.TestCase):
    def test_source_ids_and_dormant_group_survive_snapshot(self):
        c=config();r=Replay(c,ROOT)
        self.assertEqual(r.active_group_indices,[0]);self.assertEqual(set(r.records),{0,2})
        self.assertFalse(r.request_ids(1));self.assertFalse(r.contexts(3))
        with tempfile.TemporaryDirectory() as folder:
            saved,evidence=r.snapshot(folder);other=Replay(saved,folder)
            self.assertEqual(other.active_group_indices,[0]);self.assertEqual(saved['parallel'],c['parallel'])
            self.assertEqual(evidence['admitted_group_indices'],[0]);self.assertEqual(evidence['inactive_group_indices'],[1])
            self.assertEqual(other.routes([0,2],0,0),r.routes([0,2],0,0))

    def test_legacy_sparse_still_requires_a_request_in_each_group(self):
        c=config();c['route_replay']['request_layout']='sparse_explicit/1'
        with self.assertRaisesRegex(ValueError,'each sparse EP'):Replay(c,ROOT)

    def test_explicit_mode_rejects_empty_sources_and_unsupported_observers(self):
        for change in ('empty','nonpersistent','full','whole_call_replication','inflight_helpers','contribution_tracking','source_ack_watchdog'):
            c=config()
            if change=='empty':c['route_replay']['requests']=[]
            elif change=='nonpersistent':del c['transport']
            elif change=='full':c['schema_version']='moe-scaleout-service/2'
            else:c[change]={}
            with self.subTest(change=change),self.assertRaises(ValueError):Replay(c,ROOT)

    def test_dormant_group_cannot_be_built_as_a_fake_completed_graph(self):
        c=config();r=Replay(c,ROOT)
        with self.assertRaisesRegex(ValueError,'inactive EP'):make_wave(c,[1,3],1,0,1,r)
        self.assertTrue(make_wave(c,[0,2],0,0,1,r)['messages'])

    def test_real_scheduler_completes_only_admitted_groups_without_fake_steps_or_kv(self):
        class Preflight:
            def accept_wave(self,wave,now):pass
        c=config();r=Replay(c,ROOT)
        with tempfile.TemporaryDirectory() as folder,patch('moe_ub_cosim.scaleout_service.export_wave'):
            f=Frontend(c,4,1,r,message_preflight=Preflight())
            self.assertEqual(set(f.schedulers),{0,2});self.assertEqual({q.id for q in f.requests},{0,2})
            event=dict(now_ns=0,completed=[],available_rank_ids=list(range(4)),decision_id=0)
            initial=f.decide(event,Path(folder),None);self.assertEqual(len(initial['submissions']),1)
            for step in range(2):
                now=(step+1)*100000;event.update(now_ns=now,decision_id=step+1,completed=[dict(graph_id=f'g0.w{step}',finished_ns=now)])
                reply=f.decide(event,Path(folder),None)
            self.assertTrue(reply['service_finished']);self.assertEqual(f.steps,[2,0])
            self.assertTrue(all(s.kv.npu_pool.used_bytes()==0 for s in f.schedulers.values()))
            report=dict(history=f.history,scheduler_calls=f.calls,active_source_ranks=[0,2],admitted_group_indices=f.admitted_groups,
                requests=[dict(id=q.id,state='OBSERVATION_ENDED',scheduler_state=q.status.name,
                    initial_context_tokens=r.records[q.id]['context_tokens'],intertoken_ns=q.itl) for q in f.requests])
            verify_replay_requests(c,r,report)
            for field in ('admitted_group_indices','history'):
                changed=deepcopy(report)
                if field=='admitted_group_indices':changed[field]=[0,1]
                else:changed[field][0]['group_index']=1
                with self.subTest(field=field),self.assertRaises(ValueError):verify_replay_requests(c,r,changed)

    def test_active_group_is_selected_by_requests_not_lowest_group_index(self):
        c=config();c['route_replay']['requests']=[]
        original=json.loads((ROOT/'examples/scaleout_route_replay/service.json').read_bytes())
        c['route_replay']['requests']=[original['route_replay']['requests'][1]]
        r=Replay(c,ROOT);self.assertEqual(r.active_group_indices,[1]);self.assertEqual(set(r.records),{1})


if __name__=='__main__':unittest.main()
