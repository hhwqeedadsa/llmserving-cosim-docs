"""Execution dependency reachability, without counterfactual timing or ACKs."""
from collections import defaultdict,deque

def expanded(waves):
    """Expand phase completion and sequential graph-retirement constraints."""
    nodes={};parents={};groups=defaultdict(list)
    for wave in waves:
        graph=wave['graph_id'];assert graph not in {g for seq in groups.values() for _,g in seq}
        groups[wave['group_index']].append((wave['decode_step'],graph))
        assert set(map(int,wave['rank_nodes']))==set(wave['rank_ids'])
        for rank,items in wave['rank_nodes'].items():
            rank=int(rank)
            for node in items:
                key=graph,rank,node['id'];assert key not in nodes
                nodes[key]=node;parents[key]={(graph,rank,d) for d in node['deps']}
        claimed=set()
        for gate in wave['phase_completion']['gates']:
            members=gate['members'];assert len(members)==len(wave['rank_ids']) and {m['rank'] for m in members}==set(wave['rank_ids'])
            keys=[(graph,m['rank'],m['node']) for m in members]
            assert len(set(keys))==len(keys) and not claimed.intersection(keys)
            assert all(nodes[k]['kind']=='GATE' for k in keys)
            needed=set().union(*(parents[k] for k in keys))
            for k in keys:parents[k]=set(needed)
            claimed.update(keys)
        assert {k for k,n in nodes.items() if k[0]==graph and n['kind']=='GATE'}==claimed
    for sequences in groups.values():
        sequences.sort();assert [s for s,_ in sequences]==list(range(len(sequences)))
        for (_,before),(_,after) in zip(sequences,sequences[1:]):
            referenced=set().union(*(p for k,p in parents.items() if k[0]==before))
            leaves={k for k in nodes if k[0]==before and k not in referenced}
            assert leaves
            for key in nodes:
                if key[0]==after and not parents[key]:parents[key]=set(leaves)
    assert all(parent in nodes for ps in parents.values() for parent in ps)
    children=defaultdict(set);degree={key:len(ps) for key,ps in parents.items()}
    for key,ps in parents.items():
        for parent in ps:children[parent].add(key)
    ready=deque(key for key,n in degree.items() if n==0);ordered=[]
    while ready:
        key=ready.popleft();ordered.append(key)
        for child in children[key]:
            degree[child]-=1
            if degree[child]==0:ready.append(child)
    assert len(ordered)==len(nodes),'cyclic execution dependencies'
    return nodes,parents,children

def closure(roots,children):
    pending=deque(roots);seen=set(roots)
    while pending:
        for child in children.get(pending.popleft(),()):
            if child not in seen:seen.add(child);pending.append(child)
    return seen

def path_to(roots,target,children):
    pending=deque(sorted(roots));previous={key:None for key in roots}
    while pending:
        key=pending.popleft()
        if key==target:
            path=[]
            while key is not None:path.append(key);key=previous[key]
            return list(reversed(path))
        for child in sorted(children.get(key,())):
            if child not in previous:previous[child]=key;pending.append(child)
    return None
