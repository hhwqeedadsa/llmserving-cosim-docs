"""Independently replay RX byte coverage and check every published mechanism witness."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re


def load(p):
    return json.loads(p.read_bytes())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check_curve(raw, wave, record):
    ids = {m['id'] for m in wave['messages'] if m['phase'] == 'dispatch' and m['layer'] == 0}
    messages = [m for m in raw['service_transport']['messages'] if m['id'] in ids]
    assert record['messages'] == messages and len(ids) == len(messages) == 29
    slots = defaultdict(list)
    for m in messages:
        slots[m['stream']].append(dict(begin=m['begin'], end=m['end'], dst=m['dst'], bits=bytearray(m['bytes'])))
    data, ack = defaultdict(int), defaultdict(int)
    cap = raw['packet_capture']
    increments = []
    for row in cap['events']:
        p = dict(zip(cap['columns'], row))
        if p['ip_protocol'] != 17 or p['event'] != cap['event_codes']['rx']:
            continue
        for slot in slots.get(p['message_id'], []):
            if p['node'] != slot['dst']:
                continue
            left = max(slot['begin'], p['sequence'])-slot['begin']
            right = min(slot['end'], p['sequence']+p['data_payload_bytes'])-slot['begin']
            if left >= right:
                continue
            fresh = slot['bits'][left:right].count(0)
            slot['bits'][left:right] = b'\1'*(right-left)
            if fresh:
                data[p['time_ns']] += fresh
                increments.append(dict(packet=p, new_payload_bytes=fresh))
    assert increments == record['actual_RX_increments']
    assert all(all(s['bits']) for rows in slots.values() for s in rows)
    for m in messages:
        ack[m['ack_ns']] += m['bytes']
    for field, values in (('unique_received_curve_ns_bytes', data), ('fully_confirmed_message_curve_ns_bytes', ack)):
        points = [[0, 0]]
        total = 0
        for now, amount in sorted(values.items()):
            total += amount
            points.append([now, total])
        assert total == record['payload_bytes'] == 419456
        assert points == record[field]
    return len(increments)


def check_mechanism(raw, record):
    cap = raw['packet_capture']
    originals = Counter(tuple(x) for x in cap['events'])
    checked = 0
    for field in ('drop_rows', 'actual_source_ACKs', 'actual_data_ready_RX', 'final_ACK_path'):
        for p in record[field]:
            assert originals[tuple(p[c] for c in cap['columns'])] == 1
            checked += 1
    m = record['message']
    assert m in raw['service_transport']['messages']
    assert record['first_dispatch_gate'] in raw['service']['phase_completion']['gates']
    n = raw['service_transport']['native']
    source = [dict(zip(n['source_packet_columns'], x)) for x in n['source_packets']]
    events = [dict(zip(n['recovery']['columns'], x)) for x in n['recovery']['events']]
    assert record['source_packets'] == [x for x in source if x['stream'] == 65 and x['time_ns'] <= m['ack_ns']]
    assert record['recovery_events'] == [x for x in events if x['message_id'] == 65 and x['time_ns'] <= m['ack_ns']]
    assert record['final_ACK_path'][-1]['time_ns'] == m['ack_ns']
    return checked


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []

    def handle_starttag(self, tag, attrs):
        self.links.extend(v for k, v in attrs if k in ('href', 'src') and v)


def rejected(function, *args):
    try:
        function(*args)
    except AssertionError:
        return True
    raise AssertionError('corrupt published evidence was accepted')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('stage', 'delivery', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    d = a.delivery
    frozen = load(a.stage/'study/frozen-v2.json')
    if 'amendment' in frozen:
        assert sha(a.stage/'study/frozen.json') == frozen['amendment']['original_frozen_sha256']
        assert sha(a.stage/'study/compatibility.json') == frozen['amendment']['compatibility_sha256']
        assert sha(a.stage/'study/normal/result.json') == frozen['amendment']['normal_result_sha256']
    compatibility = load(a.stage/'study/compatibility.json')
    assert compatibility['passed'] and compatibility['normal_result_sha256'] == sha(a.stage/'study/normal/result.json')
    assert load(d/'raw_verification.json')['analysis_sha256'] == sha(d/'analysis.json')
    curves, mechanism = load(d/'arrival_curves.json'), load(d/'mechanism.json')
    increments = witnesses = 0
    tamper = []
    for name, curve in curves.items():
        directory = a.stage/'study'/name
        raw = load(directory/'result.json')
        wave = load(directory/'graphs/g1.w0/wave.json')
        increments += check_curve(raw, wave, curve)
        if name in mechanism:
            assert sha(directory/'result.json') == mechanism[name]['result_sha256']
            witnesses += check_mechanism(raw, mechanism[name])
        if name == 'local-w60':
            altered = deepcopy(curve)
            altered['actual_RX_increments'][0]['new_payload_bytes'] += 1
            tamper.append(dict(mutation='duplicate or invented receive bytes', rejected=rejected(check_curve, raw, wave, altered)))
            altered = deepcopy(curve)
            altered['fully_confirmed_message_curve_ns_bytes'][-1][0] -= 1
            tamper.append(dict(mutation='source confirmation before actual ACK', rejected=rejected(check_curve, raw, wave, altered)))
            altered = deepcopy(mechanism[name])
            altered['drop_rows'][0]['packet_uid'] += 1000000
            tamper.append(dict(mutation='invented dropped packet', rejected=rejected(check_mechanism, raw, altered)))
            altered = deepcopy(mechanism[name])
            altered['recovery_events'][0]['deadline_ns'] += 1
            tamper.append(dict(mutation='changed actual retry schedule', rejected=rejected(check_mechanism, raw, altered)))
    acc = mechanism['accounting']
    assert acc['restore_to_retry_ns']+acc['retry_to_ACK_ns'] == acc['restore_to_ACK_ns'] == 69846
    assert acc['same_message_ACK_delta_ns']-acc['instant_gate_slack_ns'] == acc['first_dispatch_gate_delta_ns'] == 98281
    assert acc['first_dispatch_gate_delta_ns']+acc['later_group_span_delta_ns'] == acc['group1_finish_delta_ns'] == 98113
    local, instant = mechanism['local-w60'], mechanism['instant-w60']
    assert acc['source_ACK_ns'] == local['message']['ack_ns']
    assert acc['first_complete_data_ns'] == local['message']['receive_ready_ns']
    assert acc['same_message_ACK_delta_ns'] == local['message']['ack_ns']-instant['message']['ack_ns']
    assert acc['ACKs_dropped_at_Agg132_ns'] == [x['time_ns'] for x in local['drop_rows'] if x['ip_protocol'] == 252]
    topology = load(a.stage/'study/local-w60/topology.json')
    edge = next(e for e in topology['links'] if (e['src'], e['dst']) == (128, 132))
    assert (edge['src_port'], edge['dst_port']) == (17, 1)
    parser = Links(); parser.feed((d/'report.html').read_text())
    assert len(parser.links) == 8 and all((d/x).is_file() for x in parser.links)
    md = re.findall(r'\]\(([^)]+)\)', (d/'README.md').read_text())
    assert all((d/x).is_file() for x in md)
    from PIL import Image
    for name in ('comparison.png', 'arrival_curves.png'):
        with Image.open(d/name) as im:
            assert im.width > 1000 and im.height > 500
    proof = dict(passed=True, unique_RX_increments_checked=increments, raw_mechanism_packet_witnesses=witnesses,
        tamper_checks=tamper, html_resources=len(parser.links), markdown_links=len(md),
        browser_rendering_verified=False, delivery_sha256={x.name: sha(x) for x in d.iterdir() if x.is_file()},
        verifier_sha256=sha(Path(__file__)))
    with a.output.open('x') as s:
        json.dump(proof, s, indent=2); s.write('\n')
    print(json.dumps(dict(passed=True, unique_RX_increments_checked=increments,
        raw_mechanism_packet_witnesses=witnesses, tamper_checks=4)))


if __name__ == '__main__':
    main()
