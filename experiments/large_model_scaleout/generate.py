"""Generate six audited physical fabrics through the public CLI; no network execution."""
import argparse
from collections import Counter,defaultdict,deque
import hashlib
import json
from pathlib import Path
import subprocess
import sys

CASES=('clos-300','clos-500','clos-320','clos-512','hpn-inspired-320','hpn-inspired-512')


def read(p):return json.loads(p.read_bytes())


def generate(repo,output):
    assert not output.exists();output.mkdir(parents=True)
    records=[];commands=[]
    for name in CASES:
        config=repo/'examples/scaleout_large_models'/(name+'.json');target=output/name
        cmd=[sys.executable,'-m','moe_ub_cosim','scaleout','topology','--config',str(config),'--output',str(target)]
        done=subprocess.run(cmd,cwd=repo,capture_output=True,timeout=30)
        (output/(name+'.stdout')).write_bytes(done.stdout);(output/(name+'.stderr')).write_bytes(done.stderr)
        commands.append(dict(command=cmd,cwd=str(repo),exit_code=done.returncode))
        (output/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        if done.returncode:raise RuntimeError(name+' failed; no retry')
        c=read(config);t=read(target/'topology.json');a=read(target/'audit.json')
        nodes=t['nodes'];links=t['links'];n=int(name.split('-')[-1]);assert a['counts']['gpus']==n
        counts=Counter(r['kind'] for r in nodes);assert counts['gpu']==n
        assert len({r['server'] for r in nodes if r['kind']=='gpu'})*c['deployment']['gpus_per_server']==n
        degree=Counter();edges=set();adj=defaultdict(set)
        for link in links:
            edge=tuple(sorted((link['src'],link['dst'])));assert edge not in edges;edges.add(edge)
            for end in edge:degree[end]+=1
            for x,y in (edge,edge[::-1]):adj[x].add(y)
        for node in nodes:assert degree[node['id']]<=c['port_limits'][node['kind']]
        # Independent per-plane BFS forbids GPU transit; every GPU must attach.
        for plane in range(c['fabric']['planes']):
            switches={r['id'] for r in nodes if r['kind']!='gpu' and r['plane']==plane}
            pending=deque([min(switches)]);seen={pending[0]}
            while pending:
                for peer in adj[pending.popleft()]:
                    if peer in switches and peer not in seen:seen.add(peer);pending.append(peer)
            assert seen==switches
            assert all(adj[g]&seen for g in range(n))
        assert a['all_gpu_pairs_have_packet_switch_path'] and not a['network_executed']
        for filename,first in (('topology.astra.txt',3),('topology.simai.txt',6)):
            lines=(target/filename).read_text().splitlines();header=lines[0].split()
            assert len(header)==first and int(header[0])==len(nodes) and len(lines)-2==len(links)
            sw_count=int(header[1 if first==3 else 3]);assert sw_count==len(nodes)-n
        ratios={kind:sorted({r['down_to_up_ratio'] for r in a['switch_ports'] if r['kind']==kind and r['down_to_up_ratio'] is not None})
                for kind in ('tor','agg')}
        records.append(dict(case=name,gpus=n,servers=a['counts']['servers'],pods=c['deployment']['pods'],
            gpus_per_server=c['deployment']['gpus_per_server'],tor=counts['tor'],agg=counts['agg'],core=counts['core'],
            links=len(links),planes=c['fabric']['planes'],rails=c['fabric']['rails'],nic=a['per_nic'],
            capacity_ratios=ratios,state='generated_not_executed',independent_check_passed=True,
            config_sha256=hashlib.sha256(config.read_bytes()).hexdigest()))
    result=dict(topologies=records,network_executions=0,gpu_runs=0,topology_checks_passed=True)
    (output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repository',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();generate(a.repository.resolve(),a.output.resolve())
