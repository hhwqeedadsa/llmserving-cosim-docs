"""Independent source, input, graph-rendering and copied-curve checks; no network."""
import argparse
import csv
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from moe_ub_cosim.scaleout_service import validate


def read(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[];self.ids=set()
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if 'id' in attrs:self.ids.add(attrs['id'])
        self.links.extend(attrs[k] for k in ('href','src') if k in attrs)


def verify(repo,stage,report,original,output):
    assert not output.exists();output.mkdir(parents=True)
    upstream=repo/'experiments/large_model_scaleout/upstream';sources=read(upstream/'manifest.json')
    for entry in sources:
        p=upstream/entry['file'];assert sha(p)==entry['sha256'] and p.stat().st_size==entry['bytes']
    topology_summary=read(report/'topologies.json');model_summary=read(report/'models.json')
    protocol=repo/'examples/scaleout_large_models/rdma-static-preflight.json';preflights=[];services=[];draws=[]
    for record in topology_summary['topologies']:
        case=record['case'];directory=stage/'topologies'/case;t=read(directory/'topology.json')
        cmd=[sys.executable,'-m','moe_ub_cosim','scaleout','rdma-preflight',
             '--topology',str(directory),'--protocol',str(protocol),'--output',str(output/(case+'-buffers.json'))]
        result=subprocess.run(cmd,cwd=repo,capture_output=True,timeout=15)
        (output/(case+'.stdout')).write_bytes(result.stdout);(output/(case+'.stderr')).write_bytes(result.stderr)
        assert result.returncode==0,result.stderr.decode()
        pf=read(output/(case+'-buffers.json'));assert pf['passed'] and not pf['network_executed']
        preflights.append(dict(case=case,command=cmd,cwd=str(repo),exit_code=result.returncode,
                               maximum_initial_buffer_reserve=max(r['required_bytes'] for r in pf['switches'])))
        svg=ET.parse(report/(case+'.svg'));ns={'s':'http://www.w3.org/2000/svg'}
        lines=sum(len(g.findall('s:path',ns)) for g in svg.findall('.//s:g',ns)
                  if g.attrib.get('id','').startswith('LineCollection_'))
        assert lines==len(t['links'])==record['links']
        assert (report/(case+'.pdf')).read_bytes().startswith(b'%PDF')
        assert (report/(case+'.png')).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
        draws.append(dict(case=case,svg_physical_paths=lines,expected_links=len(t['links'])))
        if record['gpus'] in (320,512):
            model=report/'inputs'/f"qwen235-{record['gpus']}.json";c=read(model)
            assert sha(model)==sha(repo/'examples/scaleout_large_models'/model.name)
            assert validate(c,t,read(protocol))==(record['gpus'],record['planes'])
            services.append(dict(case=case,model=model.name,full_service_input_validator_passed=True))
    q=read(upstream/'qwen235.json');h=q['hidden_size'];f=q['moe_intermediate_size'];l=q['num_hidden_layers'];e=q['num_experts']
    params=3*h*f*e*l + 2*q['vocab_size']*h+h
    params+=l*(2*h*q['num_attention_heads']*q['head_dim']+2*h*q['num_key_value_heads']*q['head_dim']+
               h*e+2*h+2*q['head_dim'])
    assert params>100_000_000_000
    for r in model_summary['models']:
        assert r['total_parameters']==params and r['experts_per_rank_per_layer']==e//r['ep']
    curves=read(report/'arrivals.json');curve_checks=[]
    for phase in ('dispatch','combine'):
        for ext in ('png','pdf'):assert sha(report/(phase+'-arrival.'+ext))==sha(original/(phase+'-arrival.'+ext))
        for case in ('normal','data-and-ack-loss'):
            filename=case+'-'+phase+'-curve.csv'
            assert sha(report/filename)==sha(original/filename)
            with (report/filename).open() as stream:rows=[{k:int(v) for k,v in r.items()} for r in csv.DictReader(stream)]
            assert all(a['time_ns']<=b['time_ns'] and a['accepted_bytes']<=b['accepted_bytes'] for a,b in zip(rows,rows[1:]))
            target=curves['summary'][case+'-'+phase]['slice']['logical_bytes']
            assert rows[-1]['accepted_bytes']==rows[-1]['confirmed_bytes']==target
            curve_checks.append(dict(case=case,phase=phase,logical_bytes=target,source_copy_exact=True))
    links=Links();links.feed((report/'report.html').read_text())
    for value in links.links:
        if value.startswith('#'):assert value[1:] in links.ids,value
        elif '://' not in value:assert (report/value).is_file(),value
    manifest=read(report/'artifact-manifest.json')
    for entry in manifest['files']:assert sha(report/entry['path'])==entry['sha256']
    verdict=dict(passed=True,source_files_verified=len(sources),total_model_parameters=params,
                 preflights=preflights,full_service_inputs=services,rendered_links=draws,curves=curve_checks,
                 html_links_checked=len(links.links),artifact_hashes_checked=len(manifest['files']),
                 new_network_executions=0,large_network_execution_verified=False)
    (output/'verification.json').write_text(json.dumps(verdict,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(verdict,ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('repository','stage','report','original','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();verify(a.repository.resolve(),a.stage.resolve(),a.report.resolve(),a.original.resolve(),a.output.resolve())
