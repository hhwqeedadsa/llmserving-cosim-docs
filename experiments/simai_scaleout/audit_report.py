"""Check the delivered HTML, plots, CSV linkage, hashes and inline interactions."""
import argparse
import gzip
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET


def read(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        for key,value in attrs:
            if key in ('href','src'):self.links.append(value)


def check(stage,report,output):
    assert not output.exists();manifest=read(report/'artifacts.json');data=read(report/'report-data.json')
    assert data['independent_verification']==read(stage/'independent-verification.json')
    assert data['summary']==read(stage/'summary.json')
    assert manifest['source_verification_sha256']==sha(stage/'independent-verification.json')
    for filename,digest in manifest['files'].items():assert sha(report/filename)==digest,filename
    source=(report/'report.html').read_text();links=Links();links.feed(source)
    for link in links.links:
        path=(report/link).resolve();assert path.is_relative_to(report) and path.is_file(),link
    count=0
    for family,expected_edges in (('clos',440),('hpn',960)):
        tree=ET.parse(report/(family+'-topology.svg'));ns={'s':'http://www.w3.org/2000/svg'}
        groups=[e for e in tree.findall('.//s:g',ns) if e.get('id','').startswith('LineCollection_')]
        edges=sum(len(g.findall('s:path',ns)) for g in groups);assert edges==expected_edges
        assert data['drawings'][family]['drawn_links']==edges
        for condition in ('normal','loss'):
            case=family+'-'+condition
            assert sha(report/'data'/(case+'-endpoints.csv'))==sha(stage/(case+'-dispatch-audit')/'endpoints.csv')
            assert sha(report/'data'/(case+'-verification.json'))==sha(stage/case/'verification.json')
            for phase in ('dispatch','combine'):
                prefix=case+'-'+phase
                assert sha(report/'data'/(prefix+'-curve.csv'))==sha(stage/(prefix+'-audit')/'arrival_curve.csv')
                for filename in ('packet_events.csv','messages.csv'):
                    assert gzip.decompress((report/'data'/(prefix+'-'+filename+'.gz')).read_bytes())==(stage/(prefix+'-audit')/filename).read_bytes()
                count+=1
    for name,record in manifest['figures'].items():
        assert (report/(name+'.png')).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
        assert (report/(name+'.pdf')).read_bytes().startswith(b'%PDF')
        ET.parse(report/(name+'.svg'))
    script=re.search(r'<script>(.*?)</script>',source,re.S).group(1)
    # Test the exact inline script in V8 with a minimal DOM. No browser/layout claim.
    harness='''const vm=require('vm');const assert=require('assert');
const elements={case:{value:'clos-normal'},rank:{value:''},endrows:{innerHTML:''},fabric:{value:'all'}};
const sections=[{dataset:{family:'clos'},hidden:false},{dataset:{family:'hpn'},hidden:false}];
const document={getElementById:id=>elements[id],querySelectorAll:()=>sections};
vm.runInNewContext(SCRIPT,{document});
assert.equal((elements.endrows.innerHTML.match(/<tr>/g)||[]).length,320);
elements.rank.value='0';elements.rank.oninput();assert.equal((elements.endrows.innerHTML.match(/<tr>/g)||[]).length,1);
elements.case.value='hpn-loss';elements.case.onchange();assert(elements.endrows.innerHTML.includes('<td>0</td>'));
elements.fabric.onchange({target:{value:'hpn'}});assert(sections[0].hidden&&!sections[1].hidden);
elements.rank.value='319';elements.rank.oninput();assert(elements.endrows.innerHTML.includes('<td>319</td>'));
process.stdout.write('inline interaction checks passed\\n');
'''.replace('SCRIPT',json.dumps(script))
    completed=subprocess.run(['node','-'],input=harness,capture_output=True,text=True,timeout=10);assert completed.returncode==0,completed.stderr
    verdict=dict(passed=True,artifact_hashes=len(manifest['files']),html_asset_links=len(links.links),
        new_320_gpu_curves=count,scientific_figures=len(manifest['figures']),
        all_topology_svg_edges=1400,inline_js_interactions=True,browser_layout_tested=False,
        original_curve_packet_endpoint_files_identical=True,source_stage=str(stage),report=str(report))
    output.write_text(json.dumps(verdict,indent=2)+'\n');print(json.dumps(verdict,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();check(a.stage.resolve(),a.report.resolve(),a.output.resolve())
