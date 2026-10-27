"""Independent small arithmetic and alignment invariants, no network runs."""
from collections import Counter
from experiments.route_alignment.analysis.common import program, sequence, stage_estimate, DATA_HEADER, ACK_BYTES


def checks():
    routes=[[[((r*19+t*11+k*23)%256) for k in range(8)] for t in range(8)] for r in range(8)]
    invariant=None
    for alignment in ('aligned','offset'):
        for phase in range(8):
            seq=sequence(routes,alignment,phase)
            for r in range(8):
                assert Counter(tuple(row[r]) for row in seq)==Counter(map(tuple,routes[r]))
                shift=phase+(r if alignment=='offset' else 0)
                assert all(seq[t][r]==routes[r][(t+shift)%8] for t in range(8))
            work,meta=program(routes,alignment,phase,{})
            streams={s['id']:(s['src'],s['dst']) for s in work['streams']}
            count=Counter()
            by_id={w['id']:w for w in work['work']}
            for w in work['work']:
                assert all(d['work']<w['id'] for d in w['after'])
                if w['kind']=='write':count[streams[w['stream']]]+=w['bytes']
            if invariant is None:invariant=count
            else:assert count==invariant
            for t,m in enumerate(meta['rounds']):
                expected={(r,e//32) for r,token in enumerate(seq[t]) for e in token if e//32!=r}
                assert {streams[by_id[i]['stream']] for i in m['dispatch']}==expected
                assert {streams[by_id[i]['stream']] for i in m['combine']}=={(b,a) for a,b in expected}
    link=dict(src=0,port=1,rate_bps=100000000000,delay_ns=1000)
    reverse=dict(src=1,port=1,rate_bps=100000000000,delay_ns=1000)
    v=stage_estimate({0:1},{0:dict(forward=[link],backward=[reverse])})
    expected=(14336+8*DATA_HEADER)*.08+2000+(2000+DATA_HEADER+ACK_BYTES)*.08
    assert abs(v['estimated_ns']-expected)<1e-8
    assert DATA_HEADER==36 and ACK_BYTES==48
    print('passed: 16 alignment/work invariants and one hand-computed link estimate')


if __name__=='__main__':checks()
