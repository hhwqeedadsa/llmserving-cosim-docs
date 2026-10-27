"""Peer memory authority and mixed-duration admission; no native execution."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.scaleout_topology import generate
from moe_ub_cosim.scaleout_transactions import validate

ROOT = Path(__file__).resolve().parents[2]


class PeerCopyTests(unittest.TestCase):
    def setUp(self):
        load = lambda name: json.loads((ROOT/'examples/scaleout_peer_copy'/name).read_text())
        self.config, self.protocol = load('work.json'), load('rdma.json')
        self.topology = generate(load('topology.json'))[0]

    def test_same_virtual_address_on_distinct_gpu_is_legal(self):
        validate(self.topology, self.protocol, self.config)
        source = self.config['operations'][0]['copy']['source']
        target = self.config['operations'][0]['copy']['target']
        self.assertEqual(source['address'], target['address'])
        self.assertNotEqual(self.config['operations'][0]['rank'], target['owner'])

    def test_cross_server_self_unknown_owner_and_nonzero_duration_rejected(self):
        for owner in (4, 0, 31, True):
            c = deepcopy(self.config); c['operations'][0]['copy']['target']['owner'] = owner
            with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(self.config); c['operations'][0]['duration_ns'] = 1
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_peer_requires_explicit_model_and_live_crossbar(self):
        c = deepcopy(self.config); c.pop('peer_copy_model')
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(self.config); c['peer_copy_model']['mode'] = 'automatic'
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        for value in (False, 1):
            t = deepcopy(self.topology); t['intra_host']['enabled'] = value
            with self.assertRaises(ValueError): validate(t, self.protocol, self.config)

    def test_peer_does_not_grant_source_owner_override_or_range_extension(self):
        for name, value in (('owner', 1), ('bytes', 32769)):
            c = deepcopy(self.config); c['operations'][0]['copy']['source'][name] = value
            with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(self.config); c['operations'][0]['copy']['target']['address'] = 8192
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_no_peer_v5_keeps_declared_copy_duration(self):
        c = json.loads((ROOT/'examples/scaleout_peer_copy/without-peer.json').read_text())
        t = deepcopy(self.topology); t['intra_host']['enabled'] = False
        validate(t, self.protocol, c)
        self.assertEqual(c['operations'][0]['duration_ns'], 123)
        self.assertNotIn('owner', c['operations'][0]['copy']['target'])


    def test_stream_only_interface_still_rejects_enabled_crossbar(self):
        from moe_ub_cosim.scaleout_streams import validate as streams
        c = self.config['connections'][0]
        descriptor = dict(schema_version='moe-rdma-stream-work/1', streams=[dict(
            id=0, src=c['src'], dst=c['dst'], plane=c['plane'], wire_port=c['request_port'],
            source_nic=c['source_nic'], destination_nic=c['destination_nic'], queue_depth=c['queue_depth'])],
            work=[dict(id=0, stream=0, bytes=1, after=[])])
        with self.assertRaisesRegex(ValueError, 'intra-host bypass'):
            streams(self.topology, self.protocol, descriptor)
        streams(self.topology, self.protocol, descriptor, transaction_peer_copies=True)

    def test_cache_does_not_share_transaction_and_stream_admission(self):
        from moe_ub_cosim.static_stream_validation import StaticStreamValidationCache
        cache = StaticStreamValidationCache()
        validate(self.topology, self.protocol, self.config, stream_cache=cache)
        validate(self.topology, self.protocol, self.config, stream_cache=cache)
        self.assertEqual(cache.hits, 1)
        from moe_ub_cosim.scaleout_streams import validate as streams
        from unittest.mock import patch
        # Same topology/protocol/descriptor, changed authority must run validation.
        with patch('moe_ub_cosim.scaleout_streams.validate') as wrapped:
            wrapped.side_effect = lambda t,p,d,**kw: streams(t,p,d,**kw)
            with self.assertRaisesRegex(ValueError, 'intra-host bypass'):
                cache.validate(self.topology, self.protocol, dict(schema_version='moe-rdma-stream-work/1',
                    streams=[dict(id=c['id']*2+i, src=c['dst'] if i else c['src'], dst=c['src'] if i else c['dst'],
                                  plane=c['plane'], wire_port=c['response_port'] if i else c['request_port'],
                                  source_nic=c['destination_nic'] if i else c['source_nic'],
                                  destination_nic=c['source_nic'] if i else c['destination_nic'], queue_depth=c['queue_depth'])
                             for c in self.config['connections'] for i in (0,1)],
                    work=[dict(id=0,stream=0,bytes=1,after=[])]))
            self.assertEqual(wrapped.call_count, 1)

if __name__ == '__main__': unittest.main()
