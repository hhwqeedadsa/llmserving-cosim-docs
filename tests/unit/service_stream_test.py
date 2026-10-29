"""Persistent messages retain ASTRA identity, bounds and independent timing."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.service_transport import validate,PersistentMessagePreflight
from moe_ub_cosim.service_stream_records import reconstruct
from moe_ub_cosim.scaleout_topology import generate

ROOT=Path(__file__).resolve().parents[2]


def inputs():
    messages=[dict(id=101,tag=101,src=0,dst=1,plane=0,bytes=200,layer=0,phase='dispatch'),
              dict(id=102,tag=102,src=0,dst=1,plane=0,bytes=100,layer=0,phase='combine')]
    work=[dict(id=101,stream=7,bytes=200,begin=0,end=200,ready_ns=0,published_ns=0,accepted_ns=4,acked_ns=5),
          dict(id=102,stream=7,bytes=100,begin=200,end=300,ready_ns=0,published_ns=0,accepted_ns=7,acked_ns=8)]
    actual=[dict(m,stream=7,begin=w['begin'],end=w['end'],ready_ns=0,published_ns=0,
                 receive_ready_ns=w['accepted_ns'],ack_ns=w['acked_ns']) for m,w in zip(messages,work)]
    stream=dict(id=7,src=0,dst=1,plane=0,source_nic=1,destination_nic=1,published_bytes=300,
                source_data_packets=3,source_payload_bytes=300,snd_una=300)
    events=[[1,0,1,0,1,7,17,100,100],[2,1,1,1,1,7,17,100,100],
            [3,0,1,0,2,7,17,0,100],[4,1,1,1,2,7,17,0,100],
            [5,0,1,1,3,7,252,200,0],[6,0,1,0,4,7,17,200,100],
            [7,1,1,1,4,7,17,200,100],[8,0,1,1,5,7,252,300,0]]
    native=dict(streams=[stream],work=work,recovery=dict(config=dict(mode='selective_repeat/1',receive_buffer_bytes=256)))
    result=dict(native_network_executed=True,messages=[],gpu_count=2,passed=True,finish_ns=9,
        service_transport=dict(kind='astra_persistent_streams/1',messages=actual,native=native),
        packet_capture=dict(complete=True,omitted_events=0,selection=dict(start_ns=0,end_ns=10,nodes=[]),
            event_codes=dict(tx=0,rx=1,drop=2),protocol_codes=dict(data=17),events=events,
            columns=['time_ns','node','port','event','packet_uid','message_id','ip_protocol','sequence','data_payload_bytes']))
    waves=[dict(graph_id='g0.w0',group_index=0,decode_step=0,routes=[dict(layer=0)],messages=messages)]
    return result,waves


class RecordChecks(unittest.TestCase):
    def test_same_stream_separate_message_intervals_and_selective_acceptance(self):
        r,w=inputs();original=deepcopy(r)
        summary,rows,endpoints,packets,curve=reconstruct(r,w,'g0.w0',0,'dispatch')
        self.assertEqual((summary['slice']['logical_bytes'],summary['slice']['data_ready_ns'],
                          summary['slice']['source_confirmed_ns']),(200,4,5))
        self.assertEqual((rows[1]['data_ready_ns'],rows[1]['ack_ns']),(7,8))
        self.assertEqual(next(x for x in curve if x['time_ns']==2)['accepted_bytes'],0)
        self.assertEqual(next(x for x in curve if x['time_ns']==2)['unique_arrival_bytes'],100)
        self.assertEqual(endpoints[1]['accepted_bytes'],300)
        self.assertEqual(packets[0]['stream_id'],7)
        self.assertEqual(packets[0]['semantic_message_ids'],'101')
        self.assertEqual(r,original)

    def test_packet_identity_and_completion_contradictions_rejected(self):
        for kind in ('time','port','boundary','confirm','capture','counter','stream'):
            r,w=inputs()
            if kind=='time':r['service_transport']['messages'][0]['ack_ns']=1
            elif kind=='port':r['packet_capture']['events'][1][2]=2
            elif kind=='boundary':r['packet_capture']['events'][0][7]=150
            elif kind=='confirm':r['packet_capture']['events'][4][7]=300
            elif kind=='capture':r['packet_capture']['complete']=False
            elif kind=='counter':r['service_transport']['native']['streams'][0]['source_payload_bytes']+=1
            elif kind=='stream':r['service_transport']['messages'][0]['stream']=99
            with self.subTest(kind=kind),self.assertRaises(ValueError):reconstruct(r,w,'g0.w0',0,'dispatch')

    def test_missing_final_ack_keeps_data_ready_but_not_confirmation(self):
        r,w=inputs();r['passed']=False;r['packet_capture']['events'].pop()
        r['service_transport']['messages'][1]['ack_ns']=None
        r['service_transport']['native']['work'][1]['acked_ns']=-1
        r['service_transport']['native']['streams'][0]['snd_una']=200
        summary,*_=reconstruct(r,w,'g0.w0',0,'combine')
        self.assertEqual(summary['slice']['data_ready_ns'],7)
        self.assertIsNone(summary['slice']['source_confirmed_ns'])


class PreflightChecks(unittest.TestCase):
    def setUp(self):
        folder=ROOT/'examples/astra_stream_service'
        self.service=json.loads((folder/'service.json').read_bytes())
        self.protocol=json.loads((folder/'rdma.json').read_bytes())
        self.topology,_=generate(json.loads((folder/'topology.json').read_bytes()))

    def wave(self,identity=1,size=512,dst=1):
        return dict(messages=[dict(id=identity,src=0,dst=dst,plane=0,bytes=size)])

    def test_no_legacy_modulo_collision_and_atomic_budget(self):
        config=self.service['transport'];config['limits']['max_work']=2
        checker=PersistentMessagePreflight(self.topology,self.protocol,config)
        checker.accept_wave(self.wave(1),0)
        with self.assertRaisesRegex(ValueError,'1MiB'):checker.accept_wave(self.wave(2,2**20+1),0)
        checker.accept_wave(self.wave(50001),0)
        with self.assertRaisesRegex(ValueError,'work budget'):checker.accept_wave(self.wave(3),0)

    def test_unsupported_modes_and_ambiguous_bindings_reject_before_network(self):
        for kind in ('local','isolated','binding','capture'):
            t,p,c=deepcopy(self.topology),deepcopy(self.protocol),deepcopy(self.service['transport'])
            if kind=='local':t['intra_host']['enabled']=True
            elif kind=='isolated':c['transport_recovery']['on_exhausted']='isolate_stream'
            elif kind=='binding':c['streams'][1].update(src=c['streams'][0]['src'],dst=c['streams'][0]['dst'])
            elif kind=='capture':p['packet_capture']['nodes']=[0]
            with self.subTest(kind=kind),self.assertRaises(ValueError):validate(c,t,p)


if __name__=='__main__':unittest.main()
