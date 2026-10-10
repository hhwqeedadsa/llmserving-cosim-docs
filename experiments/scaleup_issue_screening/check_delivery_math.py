"""Checks independent queue integrals, incomplete coverage and connected paths."""
import unittest
from .duration_bounds import window_bounds
from .path_analysis import queue_duration, reconstruct
from .path_replay import shortest_ports
from .compare_paths import identity


class DeliveryChecks(unittest.TestCase):
    def test_repeated_equal_size_messages_are_distinct(self):
        row=dict(operation_id='combine',src_rank=1,dst_rank=2,message_role='status',
            message_id=3,chunk_index=0,pair_byte_range=[0,32],task_byte_range=[0,32])
        self.assertNotEqual(identity(row),identity(dict(row,message_id=4)))

    def test_bounds_contain_known_occupancy(self):
        # 10 bytes for 3 ns, zero for 7 ns: area 30, duration 3.
        self.assertEqual(window_bounds(30,10,10),(3,10))
        self.assertEqual(window_bounds(0,10,10),(0,0))
        self.assertEqual(window_bounds(15,10,10),(1,10))
        with self.assertRaises(ValueError):window_bounds(101,10,10)

    def test_exact_disjoint_and_instantaneous(self):
        states=[('initial',0,0),('update',2,10),('update',5,0),
                ('update',7,20),('update',8,0),('update',9,100),('update',9,0),('final',10,0)]
        rows=[dict(event_kind=k,time_ns=t,voq_waiting_bytes=v,fifo_waiting_bytes=0) for k,t,v in states]
        r=queue_duration(rows,0,10)
        self.assertEqual(r['voq'],dict(nonempty_ns=4,max_contiguous_ns=3,byte_ns=50,intervals=2))
        self.assertEqual(r['fifo']['nonempty_ns'],0)
        with self.assertRaises(ValueError):queue_duration(rows[:-1],0,10)
        with self.assertRaises(ValueError):queue_duration(rows[1:],0,10)

    def test_all_equal_shortest_paths(self):
        topology=dict(nodes=[dict(node_id=n,kind='DEVICE' if n in (0,4) else 'SWITCH') for n in range(5)],
            links=[dict(src=a,dst=b,src_port=b,dst_port=a) for a,b in ((0,1),(1,2),(1,3),(2,4),(3,4))])
        self.assertEqual(shortest_ports(topology,0,4),{(1,2),(1,3),(2,4),(3,4)})

    def test_path_delivery_and_missing_hop(self):
        p=lambda n,t:dict(node_id=n,port_id=0,start_ns=t,busy_until_ns=t+2,ranges=[[0,8]])
        packets={10:[p(1,3),p(2,7)]}
        links={(0,1):{},(1,0,'egress'):dict(target=2,delay_ns=2),
               (2,0,'egress'):dict(target=3,delay_ns=2)}
        transfer=dict(plan=dict(payload_regions=[dict(begin=0,end=8,kind='business')]),
            actual=dict(src_ub_node=0,dst_ub_node=3,send_start_ns=0,data_ready_ns=11,committed_segments=1))
        self.assertEqual(reconstruct(packets,transfer,links,{})[0]['arrival_ns'],11)
        with self.assertRaises(ValueError):reconstruct({10:[p(1,3)]},transfer,links,{})
        transfer['actual']['data_ready_ns']=12
        with self.assertRaises(ValueError):reconstruct(packets,transfer,links,{})


if __name__=='__main__':unittest.main()
