"""Audit native timer arm/cancel/fire against actual frontend decisions."""
from .scaleout_topology import fields,integer,require


def audit(config,result,frontend):
    option=config['whole_call_replication'];report=result['service']['control_wakeup']
    fields(report,'schema_version max_events events pending_deadline_ns','service wakeup report')
    require(report['schema_version']=='moe-service-wakeup-report/1' and
            report['max_events']==option['max_wakeup_events'],'effective wakeup report differs')
    expected=[];pending=None;ticket=0
    def record(kind,now,active):
        expected.append(dict(sequence=len(expected),time_ns=now,kind=kind,deadline_ns=active[0],ticket=active[1]))
    for index,row in enumerate(frontend['transcript']):
        event,reply=row['event'],row['reply'];now=event['now_ns']
        require(event['decision_id']==index and 'wakeup_ns' in event,'missing native wakeup decision identity')
        fired=event['wakeup_ns']
        if fired is not None:
            require(type(fired) is int and pending is not None and fired==pending[0]==now,'foreign/late wakeup')
            record('fired',now,pending);pending=None
        require(pending is None or pending[0]>=now,'missed native wakeup deadline')
        target=reply['wake_at_ns']
        if target is not None:integer(target,'wakeup target',now+1,4999999)
        if (pending and target==pending[0]) or (pending is None and target is None):continue
        if pending:record('cancelled',now,pending);pending=None
        if target is not None:
            pending=(target,ticket);ticket+=1;record('armed',now,pending)
    require(pending is None and report['pending_deadline_ns'] is None and
            expected==report['events'] and len(expected)<=report['max_events'],
            'native wakeup lifecycle differs from actual control transcript')
    return dict(schema_version=report['schema_version'],events=len(expected),pending_deadline_ns=None)
