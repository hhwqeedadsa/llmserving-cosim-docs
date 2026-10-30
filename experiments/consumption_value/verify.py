"""Validate published artifacts against public diagnosis outputs, not raw truth."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


class Resources(HTMLParser):
    def __init__(self):super().__init__();self.paths=[]
    def handle_starttag(self,tag,attrs):
        self.paths.extend(v for k,v in attrs if (tag=='a' and k=='href') or (tag=='img' and k=='src'))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True)
    p.add_argument('--delivery',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    evidence=load(a.delivery/'analysis.json');count=0
    assert evidence['new_networks']==evidence['builds']==0 and all(c['exit_code']==0 for c in evidence['commands'])
    assert all(sha(ROOT/path)==digest for path,digest in evidence['source_sha256'].items())
    for key,case in evidence['cases'].items():
        diagnosis=a.study/'diagnostics'/key
        assert case['summary']==load(diagnosis/'summary.json')
        public=load(diagnosis/'calls.json');assert len(public)==len(case['calls'])
        for source,row in zip(public,case['calls']):
            clean=json.loads(json.dumps(source))
            for b in clean['bundles']:b.pop('experts',None)
            assert clean==row
            original=max(b['original_ready_ns'] for b in row['bundles'])
            winner=max(b['winner_ready_ns'] for b in row['bundles'])
            assert row['recorded_frontier_advance_ns']==original-winner and original>=winner
            for b in row['bundles']:
                if b['buffer_released_ns'] is not None:assert b['minimum_extra_hold_ns']==max(0,winner-b['buffer_released_ns'])
            count+=1
    parser=Resources();parser.feed((a.delivery/'report.html').read_text())
    assert parser.paths and all((a.delivery/path).resolve().is_file() for path in parser.paths)
    markdown=0
    for path in [ROOT/'docs/contribution_frontier.md',ROOT/'examples/contribution_frontier/README.md',
                 ROOT/'fixtures/contribution_frontier/README.md',ROOT/'experiments/consumption_value/README.md',a.delivery/'README.md']:
        for target in re.findall(r'\]\(([^)]+)\)',path.read_text()):
            if target.startswith(('http:','https:','#')):continue
            assert (path.parent/target.split('#')[0]).resolve().is_file(),(path,target);markdown+=1
    proof=dict(passed=True,calls_checked=count,public_commands=len(evidence['commands']),new_networks=0,
               html_resources=len(parser.paths),markdown_links=markdown,source_hashes_verified=True,
               png_viewed_by_verifier=False,browser_rendering_verified=False,
               files_sha256={p.name:sha(p) for p in a.delivery.iterdir() if p.is_file()})
    with a.output.open('x') as stream:json.dump(proof,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(dict(passed=True,calls_checked=count,html_resources=len(parser.paths),markdown_links=markdown)))


if __name__=='__main__':main()
