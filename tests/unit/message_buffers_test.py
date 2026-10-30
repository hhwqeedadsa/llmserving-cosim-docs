"""Independent source/destination packet and buffer lifetime corruption checks."""
from copy import deepcopy
import unittest

from moe_ub_cosim.message_buffers import audit, configuration, digest, marker, packet_writes, copy_peak_bounds


def fixture():
    config=dict(schema_version='moe-service-message-buffers/1',content='deterministic_message_marker/1',
                capacity_bytes_per_gpu=16,max_retained_bytes=32,max_messages=2,max_events=16,
                max_payload_capture_bytes=64)
    transport=dict(limits=dict(max_work=2),message_buffers=config)
    messages=[];rows=[];events=[]
    for identity,src,ready,ack,address in [(1,0,2,4,0),(2,1,3,5,4)]:
        messages.append(dict(id=identity,src=src,dst=2,bytes=4,stream=identity,begin=0,end=4,
                             ready_ns=0,published_ns=0,receive_ready_ns=ready,ack_ns=ack))
        rows.append(dict(id=identity,src=src,dst=2,bytes=4,source_address=0,target_address=address,
            received_bytes=4,source_digest_fnv64=digest(bytes([identity,0,0,0])),
            target_digest_fnv64=digest(bytes([identity,0,0,0])),post_ns=0,receive_ready_ns=ready,
            ack_ns=ack,scope_finished_ns=6,target_released_ns=6,source_live=False,target_live=False))
    def event(i,kind,owner,address,now,used,total,checksum='0000000000000000'):
        events.append(dict(kind=kind,message=i,owner=owner,address=address,bytes=4,offset=0,time_ns=now,
                           used_bytes=used,global_used_bytes=total,digest_fnv64=checksum))
    event(1,'source_acquired',0,0,0,4,8);event(1,'target_acquired',2,0,0,4,8)
    event(2,'source_acquired',1,0,0,4,16);event(2,'target_acquired',2,4,0,8,16)
    for i,ready in [(1,2),(2,3)]:
        for kind in ('target_write','target_ready'):event(i,kind,2,(i-1)*4,ready,8,16,rows[i-1]['target_digest_fnv64'])
    event(1,'source_released',0,0,4,0,12,rows[0]['source_digest_fnv64'])
    event(2,'source_released',1,0,5,0,8,rows[1]['source_digest_fnv64'])
    for i,total in [(1,4),(2,0)]:
        event(i,'scope_finished',2,(i-1)*4,6,total+4,total+4)
        event(i,'target_released',2,(i-1)*4,6,total,total,rows[i-1]['target_digest_fnv64'])
    payloads=[dict(time_ns=1,stream=i,sequence=0,hex=f'{i:02x}000000') for i in (1,2)]
    native=dict(streams=[dict(id=i,src=i-1,dst=2) for i in (1,2)],
        recovery=dict(config=dict(mode='selective_repeat/1',receive_buffer_bytes=64)),
        source_payload_packets=payloads,
        selective_repeat=dict(events=[dict(kind='payload_delivered',stream=i,sequence=0,time_ns=i+1,hex=f'{i:02x}000000') for i in (1,2)]),
        message_payload=dict(release='full_cumulative_ack/1',capture_limit_bytes=64,captured_source_bytes=8,
                             retained_bytes=0,owners=[dict(owner=o,retained_bytes=0,peak_bytes=4) for o in (0,1)]))
    buffers=dict(schema_version='moe-service-message-buffer-report/1',configuration=config,messages=rows,
                 events=events,used_bytes=0,peak_bytes=16,
                 owners=[dict(owner=o,used_bytes=0,peak_bytes=p) for o,p in [(0,4),(1,4),(2,8)]])
    columns=['time_ns','node','event','packet_uid','message_id','ip_protocol','sequence','data_payload_bytes']
    capture=[[1,0,0,11,1,17,0,4],[1,1,0,12,2,17,0,4],[2,2,1,11,1,17,0,4],[3,2,1,12,2,17,0,4]]
    result=dict(finish_ns=7,service_transport=dict(native=native,messages=messages,message_buffers=buffers),
                packet_capture=dict(columns=columns,events=capture))
    return result,transport,{1:6,2:6}


class BufferChecks(unittest.TestCase):
    def test_golden_content(self):
        self.assertEqual(marker(0x0102030405060708,10),bytes.fromhex('08070605040302010906'))
        self.assertEqual(digest(b''),'cbf29ce484222325')
        self.assertEqual(digest(b'hello'),'a430d84680aabd0b')

    def test_complete_packet_history(self):
        r,t,s=fixture();before=deepcopy(r)
        self.assertEqual(audit(r,t,s)['peak_arena_bytes'],16)
        self.assertEqual(r,before)

    def test_config_rejections(self):
        _,t,_=fixture()
        for key,value in [('max_messages',True),('max_events',13),('max_retained_bytes',0),
                          ('max_payload_capture_bytes',65*2**20),('content','BF16')]:
            with self.subTest(key=key):
                bad=deepcopy(t);bad['message_buffers'][key]=value
                with self.assertRaises(ValueError):configuration(bad)
        t['message_buffers']['extra']=1
        with self.assertRaises(ValueError):configuration(t)

    def test_payload_and_lifetime_corruption_rejected(self):
        def corrupt(r,kind):
            a=r['service_transport'];b=a['message_buffers'];n=a['native']
            if kind=='source':n['source_payload_packets'][0]['hex']='ff000000'
            if kind=='receive':n['selective_repeat']['events'][0]['hex']='ff000000'
            if kind=='missing_rx':r['packet_capture']['events'].pop(2)
            if kind=='wrong_uid':r['packet_capture']['events'][2][3]=12
            if kind=='alias':
                b['messages'][1]['target_address']=0
                for e in b['events']:
                    if e['message']==2 and e['owner']==2:e['address']=0
            if kind=='early_release':b['messages'][0]['target_released_ns']=5
            if kind=='wrong_scope':b['messages'][0]['scope_finished_ns']=5
            if kind=='occupancy':b['events'][0]['global_used_bytes']=4
            if kind=='duplicate_write':b['events'].insert(5,deepcopy(b['events'][4]))
            if kind=='live':b['messages'][0]['source_live']=True
            if kind=='copy_retained':n['message_payload']['retained_bytes']=4
            if kind=='copy_peak':n['message_payload']['owners'][0]['peak_bytes']=1000
            if kind=='missing_event':b['events'].pop()
        for kind in ['source','receive','missing_rx','wrong_uid','alias','early_release','wrong_scope',
                     'occupancy','duplicate_write','live','copy_retained','copy_peak','missing_event']:
            with self.subTest(kind=kind):
                r,t,s=fixture();corrupt(r,kind)
                with self.assertRaises(ValueError):audit(r,t,s)

    def test_feature_declaration(self):
        r,t,s=fixture();t.pop('message_buffers')
        with self.assertRaises(ValueError):audit(r,t,s)
        r['service_transport'].pop('message_buffers');r['service_transport']['native'].pop('message_payload')
        self.assertIsNone(audit(r,t,s))

    def test_go_back_n_actual_bytes(self):
        r,t,s=fixture();r['service_transport']['native']['recovery']['config']['mode']='go_back_n/1'
        self.assertTrue(audit(r,t,s)['passed'])

    def test_reordered_and_duplicate_packets_cross_message_boundary(self):
        r,_,_=fixture();a=r['service_transport'];n=a['native']
        n['streams']=[dict(id=1,src=0,dst=2)]
        messages={m['id']:m for m in a['messages']}
        messages[2].update(src=0,stream=1,begin=4,end=8)
        # A six-byte packet spans both application messages and arrives early.
        n['source_payload_packets']=[dict(time_ns=1,stream=1,sequence=2,hex='000002000000'),
            dict(time_ns=2,stream=1,sequence=0,hex='0100'),dict(time_ns=5,stream=1,sequence=0,hex='0100')]
        r['packet_capture']['events']=[[1,0,0,11,1,17,2,6],[2,0,0,12,1,17,0,2],
            [3,2,1,11,1,17,2,6],[4,2,1,12,1,17,0,2],[5,0,0,13,1,17,0,2],[6,2,1,13,1,17,0,2]]
        n['selective_repeat']['events']=[dict(kind='payload_delivered',stream=1,sequence=seq,time_ns=4,hex=data)
            for seq,data in [(0,'0100'),(2,'000002000000')]]
        writes,transmitted=packet_writes(r,messages)
        self.assertEqual(writes[1],[(4,0,b'\x01\x00'),(4,2,b'\x00\x00')])
        self.assertEqual(writes[2],[(4,0,b'\x02\x00\x00\x00')])
        self.assertEqual(transmitted,10)

    def test_copy_peak_same_ns_does_not_assume_service_callback_order(self):
        messages={1:dict(src=0,bytes=8,ready_ns=0,ack_ns=5),
                  2:dict(src=0,bytes=4,ready_ns=5,ack_ns=9)}
        self.assertEqual(copy_peak_bounds(messages),{0:(8,12)})
        messages[2]['ready_ns']=6
        self.assertEqual(copy_peak_bounds(messages),{0:(8,8)})


if __name__=='__main__':unittest.main()
