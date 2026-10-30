"""Check published explanation against saved raw packet paths and main proof."""
import argparse
from collections import Counter
import gc
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def key(row):return json.dumps(row,sort_keys=True,separators=(',',':'))


class Links(HTMLParser):
    def __init__(self):super().__init__();self.paths=[]
    def handle_starttag(self,tag,attrs):
        self.paths.extend(v for k,v in attrs if (tag=='a' and k=='href') or (tag=='img' and k=='src'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('study','delivery','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();proof=load(a.study.parent/'verification.json');assert proof['passed']
    for name in ('analysis.json','comparison.png','comparison.svg'):
        assert sha(a.delivery/name)==proof['files_sha256'][name]
    m=load(a.delivery/'mechanism.json')
    assert m['publisher_sha256']==sha(Path(__file__).with_name('publish.py'))
    packet_rows=0
    for name,case in m['cases'].items():
        r=load(a.study/name/'result.json');cap=r['packet_capture'];cols=cap['columns']
        actual=Counter(key(dict(zip(cols,row))) for row in cap['events'])
        paths=[]
        if 'damaged' in case:
            messages={x['id']:x for x in r['service_transport']['messages']}
            for group in case['damaged']:
                assert group['message']==messages[group['message']['id']]
                paths.extend(x['path'] for x in group['attempts'])
        else:
            paths=[case['data_path'],case['ack_path']]
            assert sum(x['occupied_interval_ns'] for x in case['wire_occupiers'])==case['switch133_residence_ns']
            assert all(actual[key(x['packet'])] for x in case['wire_occupiers'])
        for path in paths:
            assert all(actual[key(x)] for x in path['events'])
            packet_rows+=len(path['events'])
            assert path['source_tx_ns']==path['events'][0]['time_ns']
            assert path['destination_arrival_ns']==path['events'][-1]['time_ns']
            for node,row in path['switches'].items():
                rx=next(x for x in path['events'] if x['node']==int(node) and x['event']==1)
                tx=next(x for x in path['events'] if x['node']==int(node) and x['event']==0)
                assert row['residence_ns']==tx['time_ns']-rx['time_ns']>=0
        del r,actual;gc.collect()
    h=m['healthy_cost']
    assert h['completion_delay_ns']==h['source_tx_shift_ns']+h['total_extra_residence_ns']+h['later_critical_ready_to_ack_change_ns']==438
    assert sum(h['switch_residence_changes_ns'].values())==h['total_extra_residence_ns']==453
    parser=Links();parser.feed((a.delivery/'report.html').read_text())
    assert parser.paths and all((a.delivery/x).is_file() for x in parser.paths)
    links=re.findall(r'\]\(([^)]+)\)',(a.delivery/'README.md').read_text())
    assert links and all((a.delivery/x).is_file() for x in links)
    probes=[load(p) for p in (a.study.parent/'monitors').rglob('probe.json')]
    assert all(p['status']=='completed' and p['child_exit_code']==0 for p in probes)
    final=dict(passed=True,main_verification_sha256=sha(a.study.parent/'verification.json'),
        unique_networks=proof['unique_networks'],calls_checked=proof['calls_checked'],
        raw_packet_rows_checked=packet_rows,healthy_queue_accounting_ns=438,
        html_resources=len(parser.paths),markdown_links=len(links),browser_rendering_verified=False,
        files_sha256={p.name:sha(p) for p in a.delivery.iterdir() if p.is_file()})
    with a.output.open('x') as s:json.dump(final,s,indent=2);s.write('\n')
    print(json.dumps(dict(passed=True,networks=proof['unique_networks'],raw_packet_rows=packet_rows)))


if __name__=='__main__':main()
