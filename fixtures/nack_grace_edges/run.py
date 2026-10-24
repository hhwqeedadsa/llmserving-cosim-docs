"""One execution of each frozen edge input; source-isolation nonzero is intentional."""
import argparse
from collections import defaultdict,deque
from pathlib import Path
import sys
from stage import load,save,sha,run


def preview(topology,seed):
    from fixtures.live_ecmp.audit import murmur
    adjacency=defaultdict(list);gpu=sum(n['kind']=='gpu' for n in topology['nodes'])
    for e in topology['links']:
        adjacency[e['src']].append((e['dst'],e['src_port']));adjacency[e['dst']].append((e['src'],e['dst_port']))
    paths=[]
    for down in (False,True):
        dead={(8,10),(10,8)} if down else set();distance={0:0};q=deque([0])
        while q:
            node=q.popleft()
            for peer,_ in adjacency[node]:
                if (node,peer) in dead or peer in distance:continue
                distance[peer]=distance[node]+1
                if peer>=gpu:q.append(peer)
        node=6;path=[node]
        # Source binds physical NIC1, not the switch five-tuple hash.
        node=next(peer for peer,port in adjacency[node] if port==1);path.append(node)
        while node!=0:
            choices=[peer for peer,_ in adjacency[node] if (node,peer) not in dead and distance.get(peer,-2)==distance[node]-1]
            index=murmur([0x0b000001+32*6,0x0b000001,21012|(21012<<16)],seed+node)%len(choices)
            node=choices[index];path.append(node);assert len(path)<20
        paths.append(path)
    assert (10,8) in list(zip(paths[0],paths[0][1:])) and (10,8) not in list(zip(paths[1],paths[1][1:]))
    assert paths[0]!=paths[1]
    return dict(initial_route=paths[0],updated_route=paths[1],
        first_arrival_at_10_strictly_after_fault='four >=1000ns propagation hops plus positive serialization',
        offered_serialization_lower_bound_ns=65536*8*10**9//100000000000,
        scope='static independent hash/BFS preflight, not actual queue or cancellation evidence')


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();inputs=s/'inputs';runtime=load(s/'runtime.json')
    assert not (s/'network').exists()
    assert all(sha(s/name)==h for name,h in load(s/'inputs.json')['files'].items())
    assert all(sha(name)==h for name,h in runtime['binaries'].items())
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    run(repo,s,'topology',[*cli,'topology','--config',inputs/'topology.json','--output',s/'topology'])
    topology=load(s/'topology/topology.json')
    from moe_ub_cosim.scaleout_streams import validate
    from moe_ub_cosim.scaleout_transactions import validate as txvalidate
    for name in load(s/'inputs.json')['conditions']:
        (txvalidate if name=='transaction' else validate)(topology,load(inputs/(name+'-protocol.json')),load(inputs/(name+'-work.json')))
    save(s/'preflight.json',preview(topology,11));(s/'network').mkdir()
    for name in load(s/'inputs.json')['conditions']:
        kind='transaction' if name=='transaction' else 'stream'
        backend=next(p for p in runtime['binaries'] if p.endswith('/'+kind+'-probe'))
        run(repo,s,name,[*cli,'rdma-'+kind+'-probe','--topology',s/'topology','--protocol',inputs/(name+'-protocol.json'),
            '--config',inputs/(name+'-work.json'),'--backend',backend,'--output',s/'network'/name],network=True,
            expected=1 if name=='source-isolated' else 0)
        r=load(s/'network'/name/'result.json')
        assert r['passed']==(name!='source-isolated')
    save(s/'run.json',dict(passed=True,network_attempts=4,expected_application_failures=['source-isolated'],
        phase_closed_for_networks=True,platform_acceptance_not_implied=True))

if __name__=='__main__':main()
