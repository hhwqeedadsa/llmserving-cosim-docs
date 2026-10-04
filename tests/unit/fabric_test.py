"""Independent structural/path checks for generated fabric (not a UB runtime test)."""
from collections import defaultdict
import json
from pathlib import Path
import unittest

from moe_ub_cosim.fabric import generate, validate

ROOT=Path(__file__).resolve().parents[2]
def read(name):return json.loads((ROOT/'fixtures/m10_fabric'/name).read_text())


class FabricTest(unittest.TestCase):
    def test_target_inventory(self):
        topology,routes,channels,audit=generate(read('target.json'),read('defaults.json'))
        self.assertEqual(audit['counts'],dict(devices=768,l1_switches=336,l2_switches=112,nodes=1216,
            links=10752,ports=21504,planes=7,active_endpoints=128,inactive_endpoints=640,route_rows=47152,channels=56448))
        ports=set()
        for link in topology['links']:
            a,ap,b,bp=(link[k] for k in ('src','src_port','dst','dst_port'))
            self.assertNotIn((a,ap),ports);self.assertNotIn((b,bp),ports);ports.update(((a,ap),(b,bp)))
            if a<768:self.assertEqual((b,bp),(768+(a//16)*7+ap,a%16))
            else:self.assertEqual((b,bp),(1104+((a-768)%7)*16+ap-16,(a-768)//7))
        self.assertEqual(len(ports),21504)
        self.assertEqual({int(c['nodeId1']) for c in channels}|{int(c['nodeId2']) for c in channels},set(range(128)))
        self.assertTrue(all(int(c['nodeId1'])//2!=int(c['nodeId2'])//2 for c in channels))

    def test_all_small_candidates_reach_correct_plane(self):
        topology,routes,channels,_=generate(read('small.json'),read('defaults.json'))
        links={};lookup={}
        for l in topology['links']:
            a=(l['src'],l['src_port']);b=(l['dst'],l['dst_port']);links[a]=b;links[b]=a
        for r in routes:
            ids=r['dstNodeId'].split('..')
            for dst in range(int(ids[0]),int(ids[-1])+1):
                key=(int(r['nodeId']),dst,int(r['dstPortId']))
                self.assertNotIn(key,lookup)
                lookup[key]=list(zip(map(int,r['outPorts'].split()),map(int,r['metrics'].split())))
        # Enumerate both uplink alternatives, including destinations on the entirely idle server.
        for src in range(12):
            for dst in range(12):
                if src==dst:continue
                for plane in range(7):
                    stack=[(src,(),None)]
                    while stack:
                        node,visited,incoming=stack.pop()
                        if node==dst:
                            self.assertEqual(incoming,plane)
                            self.assertEqual(len(visited),2 if src//4==dst//4 else 4)
                            continue
                        self.assertNotIn(node,visited)
                        for port,metric in lookup[node,dst,plane]:
                            neighbor,peerport=links[node,port]
                            if neighbor!=dst:
                                self.assertEqual(metric,1+lookup[neighbor,dst,plane][0][1])
                            stack.append((neighbor,visited+(node,),peerport))
        peers=defaultdict(set);tp=set()
        for c in channels:
            a,ap,at,b,bp,bt,priority,metric=map(int,c.values())
            self.assertEqual(ap,bp);self.assertEqual(priority,7);self.assertEqual(metric,4)
            self.assertNotIn((a,at),tp);self.assertNotIn((b,bt),tp);tp.update(((a,at),(b,bt)))
            peers[a,b].add(ap)
        self.assertEqual(set(peers),{(0,4),(1,4),(2,6),(3,6)})
        self.assertTrue(all(x==set(range(7)) for x in peers.values()))

    def test_reject_before_generation(self):
        cases=[]
        for key in ('max_nodes','max_links','max_route_rows','max_channels'):
            c=read('small.json');c['budget'][key]=1;cases.append(c)
        c=read('small.json');c['groups'][1]['rank_to_ub_node'][0]=0;cases.append(c)
        c=read('small.json');c['shape']['planes']=True;cases.append(c)
        c=read('small.json');c['shape']['chips_per_server']=128;cases.append(c)
        c=read('small.json');c['groups'][0]['rank_to_ub_node'][0]=12;cases.append(c)
        for c in cases:
            with self.assertRaises(ValueError):validate(c)


if __name__=='__main__':unittest.main()
