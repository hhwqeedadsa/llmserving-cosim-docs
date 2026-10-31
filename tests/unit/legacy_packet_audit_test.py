"""CPU-only record checks; these hand-written rows are not simulated traffic."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('legacy_audit',ROOT/'tests/integration/audit_legacy_native.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)


def records():
    endpoints={0:(0,4,1,1),1:(4,0,1,1)}
    columns=['time_ns','node','port','event','packet_uid','message_id','ip_protocol','sequence',
             'model_bytes','ecn','egress_queue_bytes','data_payload_bytes']
    source=[[1,0,0,4,1,0,3,11],[2,0,0,4,1,3,3,12],[3,0,0,4,1,0,3,13]]
    payload=[dict(time_ns=row[0],stream=0,sequence=row[5],hex=data.hex())
             for row,data in zip(source,[b'abc',b'def',b'abc'])]
    physical=[[row[0],0,1,0,row[7],0,17,row[5],39,0,0,3] for row in source]
    physical.extend([[10,4,1,1,11,0,17,0,39,0,0,3],[6,4,1,1,12,0,17,3,39,0,0,3]])
    deliveries=[dict(kind='payload_delivered',stream=0,sequence=seq,hex=data.hex(),sack_valid=True,
        sack_begin=seq,sack_end=seq+len(data),packet_uid=uid,time_ns=10)
        for seq,data,uid in [(0,b'abc',11),(3,b'def',12)]]
    streams=[dict(id=0,src=0,dst=4,source_nic=1,destination_nic=1,source_data_packets=3,source_payload_bytes=9),
             dict(id=1,src=4,dst=0,source_nic=1,destination_nic=1,source_data_packets=0,source_payload_bytes=0)]
    return dict(source_packets=source,source_payload_packets=payload,streams=streams,
        selective_repeat=dict(events=deliveries)),dict(complete=True,omitted_events=0,columns=columns,events=physical),endpoints


class PacketAudit(unittest.TestCase):
    def test_reordered_delivery_and_retry_are_counted_by_unique_bytes(self):
        net,capture,endpoints=records();source,delivered,times,summary=audit.verify_packet_bytes(net,capture,endpoints)
        self.assertEqual(bytes(source[0][i] for i in range(6)),b'abcdef')
        self.assertEqual(bytes(delivered[0]),b'abcdef')
        self.assertEqual(times[0],[(0,3,10),(3,6,10)])
        self.assertEqual(summary['duplicate_payload_bytes'],3)
        self.assertEqual(summary['unique_transmitted_payload_bytes'],6)
        self.assertFalse(summary['complete_application_proven'])

    def test_misbound_or_incomplete_records_are_rejected(self):
        changes=[('wrong-uid',lambda n,c,e:n['selective_repeat']['events'][1].update(packet_uid=11)),
            ('wrong-body',lambda n,c,e:n['selective_repeat']['events'][1].update(hex=b'xyz'.hex())),
            ('wrong-destination',lambda n,c,e:c['events'][3].__setitem__(1,8)),
            ('wrong-rx-sequence',lambda n,c,e:c['events'][3].__setitem__(7,3)),
            ('wrong-rx-size',lambda n,c,e:c['events'][3].__setitem__(11,2)),
            ('rx-before-tx',lambda n,c,e:c['events'][3].__setitem__(0,0)),
            ('missing-tx',lambda n,c,e:c['events'].pop(0)),
            ('missing-rx',lambda n,c,e:c['events'].pop(3)),
            ('omitted-capture',lambda n,c,e:c.update(omitted_events=1)),
            ('wrong-source',lambda n,c,e:n['source_packets'][0].__setitem__(2,8)),
            ('changed-retry',lambda n,c,e:n['source_payload_packets'][2].update(hex=b'xyz'.hex())),
            ('wrong-source-stat',lambda n,c,e:n['streams'][0].update(source_payload_bytes=8)),
            ('duplicate-stream',lambda n,c,e:n['streams'].__setitem__(1,deepcopy(n['streams'][0]))),
            ('duplicate-source-uid',lambda n,c,e:n['source_packets'][2].__setitem__(7,11))]
        for name,change in changes:
            with self.subTest(name=name):
                net,capture,endpoints=records();change(net,capture,endpoints)
                with self.assertRaises((AssertionError,KeyError)):
                    audit.verify_packet_bytes(net,capture,endpoints)

    def test_mrt1_framing_rejects_truncated_or_invalid_data(self):
        import struct
        frame=struct.pack('>IIQQII',0x4d525431,4,7,4096,1,4)+(2**32-1).to_bytes(4,'big')
        self.assertEqual(audit.decode(frame)[0][:4],(7,4,4096,1))
        for bad in [frame[:31],frame[:-1],bytes(4)+frame[4:],frame[:4]+bytes(4)+frame[8:]]:
            with self.assertRaises((AssertionError,struct.error)):audit.decode(bad)

    def test_final_storage_coverage_rejects_missing_duplicate_and_extra_rows(self):
        audit.verify_complete_ids([[3,'c'],[1,'a'],[2,'b']],{1,2,3})
        audit.verify_complete_ids([],set())
        for rows in [[],[[1,'a'],[2,'b']],[[1,'a'],[2,'b'],[2,'c']],
                     [[1,'a'],[2,'b'],[4,'d']],[[1,'a'],[2,'b'],[3,'c'],[4,'d']]]:
            with self.assertRaises(AssertionError):audit.verify_complete_ids(rows,{1,2,3})


if __name__=='__main__':unittest.main()
