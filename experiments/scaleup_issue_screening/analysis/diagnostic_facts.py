"""Researcher preparation: exact local events -> restricted counters and truth.

Only the prepared input directory can be passed to diagnosis. Packet IDs,
actual path joins, source paths and the service-control manifest remain private.
"""
import argparse
from collections import Counter,defaultdict
import gzip
import json
from pathlib import Path
import sys
from moe_ub_cosim.network_facts import checked_rows
from .mechanism_screen import ports
from .workflow import digest,host,read,save

FIELDS=('voq_byte_ns','fifo_byte_ns','waiting_ns','busy_ns','queued_busy_ns','tx_bytes','tx_packets',
        'probes','blocked_probes','queue_peak_bytes','serial_work_ns','fifo_arrival_bytes')
IX={n:i for i,n in enumerate(FIELDS)}


class LocalState:
    """Exact positive-duration intervals; simultaneous moves create no wait."""
    def __init__(self,key,nominal,width=1000):
        self.key=key;self.nominal=nominal;self.width=width
        self.time=0;self.voq=0;self.fifo=0;self.busy_until=0
        self.windows={};self.episodes=[];self.active=None;self.slow=[]
        self.events=Counter();self.fifo_pending={};self.waits=Counter();self.network_waits=Counter()

    def row(self,t):return self.windows.setdefault(t//self.width,[0]*len(FIELDS))

    def advance(self,t):
        if t<self.time:raise ValueError('Local time went backwards')
        start=self.time;q=self.voq+self.fifo
        if t>start:
            if q:
                if self.active is None:self.active=dict(start_ns=start,end_ns=t,peak_bytes=q,queued_busy_ns=0,byte_ns=0)
                self.active['end_ns']=t;self.active['peak_bytes']=max(self.active['peak_bytes'],q)
                self.active['queued_busy_ns']+=max(0,min(t,self.busy_until)-start)
                self.active['byte_ns']+=q*(t-start)
            elif self.active is not None:
                self.episodes.append(self.active);self.active=None
            while start<t:
                end=min(t,(start//self.width+1)*self.width)
                busy=max(0,min(end,self.busy_until)-start)
                if q or busy:
                    r=self.row(start);dt=end-start
                    r[IX['voq_byte_ns']]+=self.voq*dt;r[IX['fifo_byte_ns']]+=self.fifo*dt
                    r[IX['waiting_ns']]+=dt if q else 0;r[IX['busy_ns']]+=busy
                    r[IX['queued_busy_ns']]+=busy if q else 0
                    r[IX['queue_peak_bytes']]=max(r[IX['queue_peak_bytes']],q)
                start=end
        self.time=t

    def event(self,t,event,d):
        self.advance(t);self.events[event]+=1
        if event=='initial':self.voq=d['voq_bytes'];self.fifo=d['fifo_bytes']
        elif event=='voq':self.voq=d['bytes']
        elif event=='fifo':self.fifo=d['bytes']
        elif event=='tx':
            r=self.row(t);r[IX['tx_bytes']]+=d['packet_bytes'];r[IX['tx_packets']]+=1
            r[IX['serial_work_ns']]+=d['completion_delay_ns']
            self.busy_until=max(self.busy_until,t+d['completion_delay_ns'])
            if d['rate_bps']<self.nominal:
                self.slow.append((t,t+d['completion_delay_ns']))
        elif event=='fifo_enqueue':self.row(t)[IX['fifo_arrival_bytes']]+=d['packet_bytes']
        elif event=='credit_decision':
            r=self.row(t);r[IX['probes']]+=1;r[IX['blocked_probes']]+=int(d['blocked'])
        elif event=='final':
            if self.active is not None:self.episodes.append(self.active);self.active=None


def prepare(run,output):
    run,output=Path(run).resolve(),Path(output).resolve()
    if output.exists():raise ValueError('Preparation output must be new')
    ex=run/'raw/execution';report=read(ex/'result.json')
    if not report['normal_exit'] or not report['network_drain']['idle']:raise ValueError('Incomplete source execution')
    capture=report['network_facts_capture'];end=capture['end_ns']
    net=read(run/'inputs/network/backend.json');public=read(run/'observed/public_config.json')
    scope=ports(net,public)
    links={};kinds={n['node_id']:n['kind'] for n in net['topology']['nodes']}
    for l in net['topology']['links']:
        for a,b in (('src','dst'),('dst','src')):
            links[l[a],l[a+'_port']]=dict(peer=l[b],peer_port=l[b+'_port'],delay=l['delay_ns'],rate=l['rate_bps'])
    states={(p['node_id'],p['port_id']):LocalState((p['node_id'],p['port_id']),links[p['node_id'],p['port_id']]['rate']) for p in capture['policy']['ports']}
    if not set(scope)<=set(states):raise ValueError('Incomplete public outlet coverage')
    obs=read(run/'inputs/observation.json');deep={(r['switch_id'],r['port_id']) for r in obs['recording']['detail_ranges']}
    output.mkdir(parents=True);(output/'input').mkdir();(output/'private').mkdir()
    previous_tx={};pending={};blocked={};credit_spells=[];counts=Counter();max_pending=0;unmatched=Counter()
    with gzip.open(output/'private/packets.jsonl.gz','wt',compresslevel=6) as packet_out:
        for row in checked_rows(ex,report):
            k=row['node_id'],row['port_id'];t=row['time_ns'];event=row['event'];d=row['data'];s=states[k]
            s.event(t,event,d);counts[event]+=1
            if event=='credit_decision':
                identity=k+(d['in_port'],d['vl'],d['packet_uid'])
                if d['blocked']:
                    blocked.setdefault(identity,dict(node_id=k[0],port_id=k[1],in_port=d['in_port'],vl=d['vl'],
                        packet_uid=d['packet_uid'],start_ns=t,last_failed_ns=t,available=d['available_cells'],required=d['required_cells']))['last_failed_ns']=t
                elif identity in blocked:
                    spell=blocked.pop(identity);spell.update(end_ns=t,resolution='same_head_eligible' if d['packet_uid'] is not None else 'generated_queue_eligible_head_identity_unknown')
                    credit_spells.append(spell)
            elif event=='fifo_enqueue':
                uid=d['packet_uid'];key=k+(uid,)
                if key in pending:raise ValueError('Packet enqueued twice before departure')
                upstream=previous_tx.pop(uid,None)
                if upstream and links[tuple(upstream['outlet'])]['peer']!=k[0]:raise ValueError('UID actual path mismatch')
                item=dict(node_id=k[0],port_id=k[1],packet_uid=uid,packet_bytes=d['packet_bytes'],fifo_enqueue_ns=t)
                if upstream:
                    item.update(upstream=upstream['outlet'],network_arrival_ns=upstream['arrival_ns'])
                    if t<upstream['arrival_ns']:raise ValueError('FIFO admission precedes wire arrival')
                    s.network_waits[t-upstream['arrival_ns']]+=1
                else:unmatched[k]+=1
                pending[key]=item;max_pending=max(max_pending,len(pending))
            elif event=='fifo_dequeue':
                item=pending.get(k+(d['packet_uid'],))
                if item is None:raise ValueError('FIFO dequeue without captured enqueue')
                item['fifo_dequeue_ns']=t;s.waits[t-item['fifo_enqueue_ns']]+=1
            elif event=='tx':
                uid=d['packet_uid'];item=pending.pop(k+(uid,),None)
                if item is not None:
                    if 'fifo_dequeue_ns' not in item:raise ValueError('Physical start before FIFO dequeue')
                    if k in deep:
                        item.update(tx_start_ns=t,completion_delay_ns=d['completion_delay_ns'],rate_bps=d['rate_bps'])
                        packet_out.write(json.dumps(item,separators=(',',':'))+'\n')
                if kinds[links[k]['peer']]!='DEVICE':
                    previous_tx[uid]=dict(outlet=k,arrival_ns=t+d['completion_delay_ns']+links[k]['delay'])
        for spell in blocked.values():
            spell.update(end_ns=end,resolution='no_captured_same_head_success');credit_spells.append(spell)
    # Private exact episodes are evaluated separately from aggregated diagnoses.
    truth=[];summaries=[]
    for k,s in sorted(states.items()):
        for e in s.episodes:
            e=dict(e,node_id=k[0],port_id=k[1]);e['duration_ns']=e['end_ns']-e['start_ns']
            e['qualifies']=e['duration_ns']>=1000 and e['peak_bytes']>=8192
            e['slow_service_overlap']=any(a<e['end_ns'] and b>e['start_ns'] for a,b in s.slow)
            e['failed_credit_witness']=any(x['node_id']==k[0] and x['port_id']==k[1] and
                x['start_ns']<e['end_ns'] and x['last_failed_ns']>=e['start_ns'] for x in credit_spells)
            truth.append(e)
        summaries.append(dict(node_id=k[0],port_id=k[1],events=dict(s.events),
            fifo_wait_histogram_ns=dict(s.waits),arrival_to_fifo_histogram_ns=dict(s.network_waits),
            packets_without_preceding_captured_wire=unmatched[k],
            voq_byte_ns=sum(r[IX['voq_byte_ns']] for r in s.windows.values()),
            fifo_byte_ns=sum(r[IX['fifo_byte_ns']] for r in s.windows.values())))
    with gzip.open(output/'input/windows.jsonl.gz','wt',compresslevel=6) as stream:
        for k in sorted(scope):
            for index,values in sorted(states[k].windows.items()):
                if index*1000>=end:continue
                stream.write(json.dumps([k[0],k[1],index,*values],separators=(',',':'))+'\n')
    meta=[]
    for k,p in sorted(scope.items()):
        meta.append({name:p[name] for name in ('switch_id','port_id','peer_node','peer_port','role','plane','peer_group','rate_bps')})
    save(output/'input/schema.json',dict(schema_version='issue-local-measurements/1',start_ns=0,end_ns=end,window_ns=1000,
        fields=list(FIELDS),ports=meta,encoding='sparse_windows_explicit_zero_only_within_declared_coverage',
        semantics='exact integrals and event counters; not instantaneous queue samples',
        hardware_scope='engineering counter assumptions; credit eligibility and service time are not claimed A3 profiler fields'))
    # End-side operations are supplied unchanged as the common application baseline.
    from moe_ub_cosim.research_storage import stored_rows
    with gzip.open(output/'input/endpoint_ops.jsonl.gz','wt') as stream:
        for r in stored_rows(run/'observed','endpoint_ops.jsonl'):stream.write(json.dumps(r,separators=(',',':'))+'\n')
    save(output/'private/truth.json',dict(episodes=truth,credit_checks=credit_spells,
        true_continuous_credit_block_intervals='not_inferred_from_probe_latches',
        event_definition='positive-duration queue episode, >=1us duration and >=8192-byte peak; adjacent windows are not events'))
    save(output/'private/ports.json',summaries)
    save(output/'summary.json',dict(schema_version='issue-diagnostic-preparation/1',source_manifest_sha256=digest(run/'manifest.json'),
        source=str(run),capture=capture,events=dict(counts),input_sha256={p.name:digest(p) for p in sorted((output/'input').iterdir())},
        private_sha256={p.name:digest(p) for p in sorted((output/'private').iterdir())},
        public_ports=len(scope),captured_ports=len(states),packet_pending_peak=max_pending,packet_pending_at_end=len(pending),
        unconsumed_last_wire_entries=len(previous_tx),credit_spells=len(credit_spells),
        qualifying_queue_events=sum(e['qualifies'] and (e['node_id'],e['port_id']) in scope for e in truth),
        program_sha256=digest(__file__),workload_scope='completed execution or requested half-open capture, whichever ends first'))
    print('Prepared',len(scope),'ports;',sum(counts.values()),'events;',len(credit_spells),'credit spells')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:prepare(a.run,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],str(a.output)+'-resources',**host())
        if r['status']!='completed':raise SystemExit(r['status'])

if __name__=='__main__':main()
