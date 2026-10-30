"""Post-execution check of fast-feedback availability for the same critical message.

Added after the two network executions; no new execution or parameter selection.
"""
import argparse
from collections import Counter
from pathlib import Path
import struct

from common import ROOT, load, sha, save, need


def check(root, output):
    analysis = load(root/'analysis/analysis.json')
    critical = analysis['cases']['local']['packet']['critical_first_dispatch_messages']
    rows = []
    for name in ('normal','local'):
        r = load(root/name/'result.json')
        n = r['service_transport']['native']
        need('selective_repeat' in n, 'selective repeat not enabled')
        messages = {m['id']:m for m in r['service_transport']['messages']}
        packets = [dict(zip(r['packet_capture']['columns'],p)) for p in r['packet_capture']['events']]
        for last in critical:
            m = messages[last['message']['id']]
            events = [e for e in n['selective_repeat']['events'] if e['stream']==m['stream'] and
                      m['ready_ns']<=e['time_ns']<=m['ack_ns']]
            feedback = [e for e in events if e['kind']=='feedback_received']
            actual = [p for p in packets if p['message_id']==m['stream'] and p['node']==m['src'] and
                      p['event']==1 and p['ip_protocol'] in (252,253) and m['ready_ns']<=p['time_ns']<=m['ack_ns']]
            need(Counter((e['time_ns'],e['packet_uid'],e['ip_protocol']) for e in feedback)==
                 Counter((p['time_ns'],p['packet_uid'],p['ip_protocol']) for p in actual),
                 'selective feedback differs from actual source RX packets')
            for e in feedback:
                magic,begin,end,valid = struct.unpack('!4sIII',bytes.fromhex(e['extension_hex']))
                need(magic==b'MSR1' and (begin,end,valid)==(e['sack_begin'],e['sack_end'],int(e['sack_valid'])),
                     'actual feedback extension differs from reported SACK')
            data = [e for e in events if e['kind']=='data_received']
            first = min((e['time_ns'] for e in feedback),default=None)
            rows.append(dict(case=name,message_id=m['id'],stream=m['stream'],src=m['src'],dst=m['dst'],
                first_receiver_data_ns=min((e['time_ns'] for e in data),default=None),
                first_source_feedback_ns=first,source_feedback_packets=len(feedback),
                nack_feedback_packets=sum(e['ip_protocol']==253 for e in feedback),
                feedback_before_fault_end=sum(e['time_ns']<100000 for e in feedback),
                fast_retransmit_events=sum(e['kind']=='fast_retransmit' for e in events),
                recovered_source_ack_ns=m['ack_ns'],actual_feedback=feedback))
    summary = dict(passed=True,diagnosis_added_after_network_execution=True,new_network_executions=0,
        actual_SACK_bytes_and_source_RX_checked=True,same_message_control=rows,
        source_file='backend/rdma/persistent_streams.cc',
        source_sha256=sha(ROOT/'backend/rdma/persistent_streams.cc'),
        source_basis='SelectiveReceive emits actual SACK/ACK or NACK; source FastRetry branch is driven by received NACK',
        scope='MSR1 selective-repeat functional reference with NACK fast retry; not IRN or production RoCE reproduction',
        interpretation='No feedback during a full-data blackhole prevents feedback-driven fast retry; this alone is not a novel optimization opportunity')
    save(output,summary)
    print(__import__('json').dumps(dict(passed=True,rows=[{k:v for k,v in row.items() if k!='actual_feedback'} for row in rows])))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    check(args.root.resolve(),args.output.resolve())
