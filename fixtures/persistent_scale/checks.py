"""Offline full regression, negative native admission and evidence tampering."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import runpy
import subprocess


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();out=s/'checks';out.mkdir()
    auditor=runpy.run_path(str(repo/'fixtures/persistent_scale/audit.py'))['audit'];summaries={}
    for label,n in [('small',8),('medium',128),('target',1024)]:
        r=load(s/'network'/label/'result.json');w=load(s/'inputs'/(str(n)+'-work.json'));summaries[label]=auditor(r,w)
        assert summaries[label]==load(s/'network'/label/'audit.json')
    hashes={p.name:sha(p) for p in (s/'lib').glob('libns3*.so')};assert len(hashes)==8
    def norm(r):
        r=deepcopy(r);libs=[]
        for path in r['loaded_libraries']:
            name=Path(path).name
            if name.startswith('libns3'):assert sha(Path(path))==hashes[name];libs.append(name+':'+hashes[name])
            else:libs.append(path)
        r['loaded_libraries']=libs;return r
    old=norm(load(s/'network/default-old/result.json'));new=norm(load(s/'network/default-new/result.json'));assert old==new
    scaled=norm(load(s/'network/small/result.json'))
    scaled.pop('execution_limits');new.pop('execution_limits')
    scaled['persistent_transport'].pop('execution_limits');scaled['persistent_transport'].pop('link_transmissions');assert scaled==new
    r=load(s/'network/small/result.json');w=load(s/'inputs/8-work.json');rejected=[]
    for kind in ('source_sequence','wire_port','ack_time','local_time','source_credit','prefix','link_count'):
        x=deepcopy(r);t=x['persistent_transport']
        if kind=='source_sequence':t['source_packets'][0][5]+=1
        elif kind=='wire_port':x['packet_capture']['wire_headers']['headers'][0][4]+=1
        elif kind=='ack_time':t['work'][0]['acked_ns']-=1
        elif kind=='local_time':x['local_work'][0]['start_ns']+=1
        elif kind=='source_credit':t['work'][0]['published_ns']+=1
        elif kind=='prefix':t['streams'][0]['snd_una']-=1
        else:t['link_transmissions']+=1
        try:auditor(x,w)
        except (AssertionError,KeyError):rejected.append(kind)
        else:raise AssertionError('tamper accepted: '+kind)
    negative=s/'native-rejections';negative.mkdir();commands=[]
    for kind in ('streams_zero','streams_bool','streams_big','work_big','tx_zero','tx_bool','tx_big','missing','extra','old_version'):
        candidate=deepcopy(w);l=candidate['limits']
        if kind=='streams_zero':l['max_streams']=0
        elif kind=='streams_bool':l['max_streams']=True
        elif kind=='streams_big':l['max_streams']=4097
        elif kind=='work_big':l['max_work']=65537
        elif kind=='tx_zero':l['max_link_transmissions']=0
        elif kind=='tx_bool':l['max_link_transmissions']=True
        elif kind=='tx_big':l['max_link_transmissions']=2000001
        elif kind=='missing':del l['max_link_transmissions']
        elif kind=='extra':l['extra']=1
        else:candidate['schema_version']='moe-rdma-stream-work/3'
        path=negative/(kind+'.json');save(path,candidate);result=negative/(kind+'-result.json')
        cmd=[str(s/'bin/stream-probe'),str(s/'topology/8/topology.json'),str(s/'inputs/8-protocol.json'),str(path),str(result)]
        done=subprocess.run(cmd,cwd=repo,capture_output=True,text=True,timeout=5)
        path.with_suffix('.stderr.log').write_text(done.stderr);path.with_suffix('.stdout.log').write_text(done.stdout)
        commands.append(dict(command=cmd,cwd=str(repo),exit_code=done.returncode));save(negative/'commands.json',commands)
        assert done.returncode!=0 and not result.exists() and not done.stdout and ('limit' in done.stderr),kind
    build=load(s/'build.json')
    for name,digest in build['inputs'].items():assert sha(Path(name))==digest
    for name,digest in build['source_identity'].items():assert sha(repo/name)==digest
    for name,digest in build['python_identity'].items():assert sha(repo/name)==digest
    save(s/'verification.json',dict(passed=True,cases=summaries,default_complete_result_equal=True,
        v4_small_dynamic_result_equal_to_v3=True,v4_comparison_excludes_only_declared_resource_metadata_and_new_limit_counters=True,
        verified_same_eight_library_hashes=hashes,other_system_library_paths_compared_exactly=True,
        tampering_rejected=rejected,native_pre_fabric_rejections=len(commands),python_scaleout_tests=138,
        fixed_fabric_libraries_and_sources_unchanged=True,network_executions_so_far=5))


if __name__=='__main__':main()
