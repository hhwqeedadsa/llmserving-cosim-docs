"""Check archived bytes, exported packet rows, page links and inline JS behavior."""
import argparse
import csv
import gzip
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess

CASES=('clean400','bottleneck25','loss50','loss75','blackout','persistent')
def read(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


class Page(HTMLParser):
    def __init__(self):super().__init__();self.links=[];self.scripts=[];self.active=False
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        for k in ('src','href'):
            if k in a:self.links.append(a[k])
        if tag=='script':assert 'src' not in a;self.active=True
    def handle_endtag(self,tag):
        if tag=='script':self.active=False
    def handle_data(self,data):
        if self.active:self.scripts.append(data)


def audit(root,report,output):
    assert not output.exists();manifest=read(report/'artifacts.json')['files'];files={str(p.relative_to(report)) for p in report.rglob('*') if p.is_file()}
    assert files==set(manifest)|{'artifacts.json'}
    for relative,item in manifest.items():
        p=report/relative;assert p.stat().st_size==item['bytes'] and sha(p)==item['sha256']
    verdict=read(root/'independent-verification.json');assert verdict['passed'] and read(report/'verification.json')==verdict
    data=read(report/'report-data.json');assert data['cases']==verdict['cases']
    page=Page();source=(report/'report.html').read_text();page.feed(source)
    for link in page.links:
        assert '://' not in link and not link.startswith('//'),'unexpected external dependency'
        p=(report/link).resolve();assert p.is_relative_to(report) and p.is_file(),link
    exported_packets=0
    for case in CASES:
        d=root/case;r=read(d/'result.json');assert read(report/'data'/(case+'-scenario.json'))==read(d/'scenario.json')
        for name in ('scenario.md','scenario.json'):
            assert sha(report/'data'/(case+'-'+name))==sha(d/name)
        c=verdict['cases'][case];meta={m['id']:dict(m,graph=w['graph_id']) for p in (d/'graphs').glob('*/wave.json') for w in [read(p)] for m in w['messages']}
        for phase in c['slices']:
            prefix=case+'-'+phase;directory=root/(prefix+'-audit')
            assert sha(report/'data'/(prefix+'-curve.csv'))==sha(directory/'arrival_curve.csv')
            assert read(report/'data'/(prefix+'-summary.json'))==read(directory/'summary.json')
            for name in ('messages.csv','packet_events.csv'):
                with gzip.open(report/'data'/(prefix+'-'+name+'.gz'),'rb') as stream:
                    assert hashlib.sha256(stream.read()).hexdigest()==sha(directory/name)
            selected={i for i,m in meta.items() if m['graph']=='g0.w0' and m['layer']==0 and m['phase']==phase}
            cap=r['packet_capture'];id_index=cap['columns'].index('message_id')
            expected=[i for i,row in enumerate(cap['events']) if row[id_index] in selected]
            seen=[]
            with gzip.open(report/'data'/(prefix+'-packet_events.csv.gz'),'rt') as stream:
                for row in csv.DictReader(stream):
                    i=int(row['capture_index']);seen.append(i)
                    assert all(int(row[k])==value for k,value in zip(cap['columns'],cap['events'][i]))
            assert seen==expected;exported_packets+=len(seen)
        x=c['hardware'];assert x['physical_gpu_count']==320 and x['server_count']==40 and x['largest_actual_batch']==6
        for ext in ('pdf','svg','png'):
            for name in data['figures']:assert (report/(name+'.'+ext)).stat().st_size>1000
    assert verdict['cases']['bottleneck25']['recovery']['timeouts_with_progress_ack_already_inflight']==86
    assert verdict['cases']['persistent']['accepted_bytes']==0
    assert [verdict['cases'][k]['finish_ns'] for k in CASES]==[171893,592996,663998,690414,702706,603090]
    # V8 DOM stub exercises all selectable conditions without claiming a browser layout test.
    harness='''const vm=require('vm');const fs=require('fs');const source=fs.readFileSync(0,'utf8');
const elements={condition:{value:'clean400',addEventListener:()=>{}},'scenario-text':{},'scenario-links':{}};
const context={document:{getElementById:id=>elements[id]}};vm.createContext(context);vm.runInContext(source,context);
for(const name of ['clean400','bottleneck25','loss50','loss75','blackout','persistent']){
 elements.condition.value=name;vm.runInContext('show()',context);
 const scenario=JSON.parse(elements['scenario-text'].textContent);
 if(scenario.physical_gpu_count!==320||scenario.largest_actual_batch!==6)throw Error(name);
 if(!elements['scenario-links'].innerHTML.includes(name+'-scenario.md'))throw Error('missing link');
 if((scenario.outcome.state==='failed')!==(name==='persistent'))throw Error('wrong outcome');
}process.stdout.write(JSON.stringify({passed:true,conditions:6,scope:'V8 DOM stub, not browser rendering'}));'''
    js=subprocess.run(['node','-e',harness],input='\n'.join(page.scripts),text=True,capture_output=True,timeout=15)
    assert js.returncode==0,js.stderr;js_result=json.loads(js.stdout)
    result=dict(passed=True,artifact_files=len(manifest),checked_links=len(page.links),exported_packet_rows=exported_packets,
        native_runs=6,expected_failures=1,js=js_result,no_network_reruns=True)
    output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();audit(a.root.resolve(),a.report.resolve(),a.output.resolve())
