"""Independent switch data FIFO and actual per-port serialisation checks."""
from collections import Counter,defaultdict,deque


def audit(r):
    cap=r['packet_capture'];assert cap['complete'] and not cap['omitted_events']
    ports={(p['node'],p['port']):p for p in r['ports']};fifo=defaultdict(deque)
    ends=Counter();dequeues=Counter();transmits=Counter();data_count=frames=0
    for row in cap['events']:
        e=dict(zip(cap['columns'],row));now=e['time_ns'];node=e['node'];key=node,e['port']
        kind=e['event'];proto=e['ip_protocol'];uid=e['packet_uid'];size=e['model_bytes']
        if kind==0:
            assert now>=ends[key],'physical serialization overlap'
            ends[key]=now+round(size*8*10**9/ports[key]['rate_bps']);frames+=1
        if node<r['gpu_count'] or proto!=17:continue
        identity=uid,e['message_id'],e['sequence'],size
        if kind==3:fifo[key].append((now,identity))
        if kind==4:
            assert fifo[key];entered,expected=fifo[key].popleft()
            assert expected==identity and entered<=now,'actual data FIFO violated'
            dequeues[key,now,identity]+=1;data_count+=1
        if kind==0:transmits[key,now,identity]+=1
        if kind==5:raise AssertionError('unexpected switch-local data drop in these new edge inputs')
    assert not any(fifo.values())
    assert dequeues==transmits,'actual data dequeue must bind same-port physical TX'
    return dict(passed=True,data_fifo_dequeues=data_count,all_protocol_serialized_frames=frames,
        scope='switch data FIFO and all-protocol physical port service; not CNP priority FIFO or performance calibration')
