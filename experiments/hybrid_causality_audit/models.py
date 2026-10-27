"""Two independent representations of a single logical Dispatch forwarder.

No platform executor, fixtures, packet timing or future observations are used.
The cache interpreter follows the pinned source; the candidate is a fixed DAG.
"""
from collections import deque
from itertools import product
import hashlib
import json


def publications(size, interval=6):
    return tuple((n, False) for n in range(interval, size+1, interval))+((size, True),)


def initial(domains, seed):
    return ((0,)*domains, (False,)*domains, (0,)*domains, seed, -1)


def cache_raw_step(state, published, sizes, *, interval=6, chunk=6):
    """Exactly one selected-source turn, including zero data or waiting."""
    tail, finished, used, last, selected = state
    tail, finished, used = list(tail), list(finished), list(used)
    domains = len(sizes)
    pending = [i for i in range(domains) if used[i] < tail[i] or not finished[i]]
    if not pending:return state,('done',)
    if selected < 0:selected = min(pending,key=lambda i:(i-last-1)%domains)
    if tail[selected] == used[selected] and not finished[selected]:
        for peer,index in enumerate(published):
            if index >= 0:tail[peer],finished[peer] = publications(sizes[peer],interval)[index]
    if tail[selected] == used[selected] and not finished[selected]:
        return (tuple(tail),tuple(finished),tuple(used),last,selected),('blocked',selected)
    peer,begin = selected,used[selected]
    end = min(tail[peer],begin+chunk);used[peer] = end
    return (tuple(tail),tuple(finished),tuple(used),peer,-1),('data' if end>begin else 'empty',peer,begin,end)


def cache_attempt(state, published, sizes, *, interval=6, chunk=6):
    """One nonempty admission, or blocked/end, erasing zero-work turns."""
    for _ in range(len(sizes)+1):
        state,value = cache_raw_step(state,published,sizes,interval=interval,chunk=chunk)
        if value[0]!='empty':return state,value
    raise AssertionError('more zero-work peers than domains')


def static_dag(sizes, seed, *, interval=6, chunk=6):
    """Input-only round-robin blocks, with explicit independent finish gates.

    This construction is claimed only for interval divisible by chunk. The
    off-default control deliberately violates that guard, never silently uses it.
    """
    assert interval > 0 and chunk > 0 and interval % chunk == 0
    queues = []
    for peer,size in enumerate(sizes):
        nodes = []
        for begin in range(0,size,chunk):
            end = min(size,begin+chunk)
            pub = min(i for i,(tail,_) in enumerate(publications(size,interval)) if tail>=end)
            nodes.append((peer,begin,end,pub))
        if size%chunk == 0: nodes.append((peer,size,size,len(publications(size,interval))-1))
        queues.append(deque(nodes))
    word, last = [], seed
    while any(queues):
        peer = next((last+offset)%len(sizes) for offset in range(1,len(sizes)+1) if queues[(last+offset)%len(sizes)])
        word.append(queues[peer].popleft());last=peer
    return tuple(word)


def dag_attempt(word, cursor, published):
    while cursor < len(word):
        peer,begin,end,publication = word[cursor]
        if published[peer] < publication:return cursor,('blocked',peer)
        cursor += 1
        if end>begin:return cursor,('data',peer,begin,end)
    return cursor,('done',)


def roots():
    for domains, choices in ((2,(0,1,5,6,7,12,13,16)),(3,(0,6,7,12,13)),(4,(0,1,6,7))):
        for sizes in product(choices,repeat=domains):
            for seed in range(domains):yield sizes,seed


def explore(sizes, seed, remaining_states, remaining_edges):
    if remaining_states < 1 or remaining_edges < 0:
        return dict(status='budget_limit',sizes=sizes,seed=seed,states=0,edges=0)
    word=static_dag(sizes,seed)
    start=((-1,)*len(sizes),initial(len(sizes),seed),0)
    queue=deque([start]);seen={start};parents={start:None};edge_count=0;digest=hashlib.sha256()
    def witness(state, action, left, right):
        actions=[action]
        while parents[state] is not None:
            previous,move=parents[state];actions.append(move);state=previous
        return dict(actions=list(reversed(actions)),source_result=left,dag_result=right)
    while queue:
        state=queue.popleft();pub,source,cursor=state
        successors=[]
        for peer,index in enumerate(pub):
            if index+1<len(publications(sizes[peer])):
                updated=list(pub);updated[peer]+=1
                successors.append((('publish',peer), (tuple(updated),source,cursor)))
        after,left=cache_attempt(source,pub,sizes);next_cursor,right=dag_attempt(word,cursor,pub)
        if left!=right:
            return dict(status='counterexample',sizes=sizes,seed=seed,states=len(seen),edges=edge_count,
                        witness=witness(state,('attempt',),left,right))
        successors.append((('attempt',),(pub,after,next_cursor)))
        for action,target in successors:
            if target==state:continue
            if edge_count>=remaining_edges:
                return dict(status='budget_limit',sizes=sizes,seed=seed,states=len(seen),edges=edge_count)
            edge_count+=1
            digest.update(json.dumps((state,action,target),separators=(',',':')).encode()+b'\n')
            if target not in seen:
                if len(seen)>=remaining_states:
                    return dict(status='budget_limit',sizes=sizes,seed=seed,states=len(seen),edges=edge_count)
                seen.add(target);parents[target]=(state,action);queue.append(target)
    return dict(status='equivalent',sizes=sizes,seed=seed,states=len(seen),edges=edge_count,
                transition_sha256=digest.hexdigest(),dag_nodes=len(word),
                data_nodes=sum(node[1]!=node[2] for node in word))


def hypothetical_control():
    """Same counts under deliberately unequal progress/consumption granularities."""
    sizes=(1,10);interval,chunk=5,6
    def run(actions):
        state=initial(2,0);pub=[-1,-1];admissions=[]
        for action in actions:
            if action[0]=='publish':pub[action[1]]+=1
            else:
                state,value=cache_attempt(state,pub,sizes,interval=interval,chunk=chunk)
                if value[0]=='data':admissions.append(value[1:])
        for _ in range(10):
            state,value=cache_attempt(state,pub,sizes,interval=interval,chunk=chunk)
            if value[0]=='data':admissions.append(value[1:])
            elif value[0]=='done':return admissions
        raise AssertionError('control did not finish')
    early=[('publish',0),('publish',1),('attempt',),('attempt',),('publish',1),('publish',1)]
    late=[('publish',0),('publish',1),('publish',1),('publish',1)]
    a,b=run(early),run(late)
    assert a!=b
    return dict(progress_interval=interval,consumption_chunk=chunk,sizes=sizes,
                early_actions=early,late_actions=late,early_admissions=a,late_admissions=b,
                deployed_configuration=False,gpu_executed=False)
