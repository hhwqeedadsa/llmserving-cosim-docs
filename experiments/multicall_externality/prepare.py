"""Freeze15 distinct locally supplied routes and four new shared-network cases."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
    with p.open('x') as stream:json.dump(v,stream,ensure_ascii=False,indent=2);stream.write('\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('known-manifest','intake','backend','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    known=load(a.known_manifest);intake=load(a.intake);m=deepcopy(known)
    expected={'900.json','901.json','905.json','1003.json','1004.json','1007.json','1008.json','1011.json','1477.json','1480.json','1481.json'}
    added=[f for f in intake['files'] if f['group']=='new_user_supplied' and f['structure_valid']]
    assert {f['name'] for f in added}==expected and set(intake['rejected_files'])=={'899.json','1479.json'}
    assert [Path(f['file']['path']).name for f in known['files']]==['1472.json','1808.json','2675.json','6447.json']
    origins={};digests=set()
    for f in m['files']:
        path=(ROOT/f['file']['path']).resolve();assert sha(path)==f['file']['sha256']
        f['file']['path']=str(path);origins[f['file_id']]=dict(identity='known_fixed_revision',dataset_revision=known['source']['dataset_revision'])
        assert f['file']['sha256'] not in digests;digests.add(f['file']['sha256'])
    for f in added:
        path=Path(f['local_path']);assert f['origin_directory_user_confirmed'] and sha(path)==f['sha256']
        assert f['sha256'] not in digests;digests.add(f['sha256']);identity='user-'+Path(f['name']).stem
        m['files'].append(dict(file_id=identity,original_path=f['original_path'],
            file=dict(path=str(path),sha256=f['sha256']),format='token_layer_map_v1'))
        origins[identity]=dict(identity='user_confirmed_directory_local_SHA256',dataset_revision=None,
            remote_revision_content_verified=False,original_path_status='historical_catalog_candidate')
    m['source'].update(kind='user_provided_model_routes',dataset_revision=None,
        source_url='https://huggingface.co/datasets/core12345/MoE_expert_selection_trace',
        usage_terms='User-supplied R1-AWQ/MMLU routes; four known fixed-revision files and11 directory-confirmed additions. Local hashes preserved. Added remote revision/content identity is unknown; no credentials or terms accepted by agent.')
    m['source']['unknown_fields']=sorted(set(m['source']['unknown_fields'])|{'dataset_revision','added_remote_content_identity'})
    save(out/'source-manifest.json',m);save(out/'source-lineage.json',dict(files=origins,intake_sha256=sha(a.intake),
        old_manifest_sha256=sha(a.known_manifest),excluded_files=intake['rejected_files']))
    prepared=out/'input'
    subprocess.run([sys.executable,'-B',str(ROOT/'examples/scaleout_multicall/prepare.py'),'--output',str(prepared),
        '--source-manifest',str(out/'source-manifest.json'),'--target-files','4',
        '--target-deadline-ns','70000','--healthy-deadline-ns','4000000','--control-deadline-ns','4000000'],cwd=ROOT,check=True)
    c=load(prepared/'service-control.json');t=load(prepared/'service-multicall.json')
    changed=deepcopy(c);changed['whole_call_replication']['calls'][0]['hedge_after_ns']=70000;assert changed==t
    summary=load(prepared/'multicall-preparation.json')
    assert (summary['target_logical_requests'],summary['healthy_logical_requests'],summary['unique_source_files'])==(4,11,15)
    backend=a.backend.resolve();pre=load(ROOT/'fixtures/multicall_deadline/pre-network.json')
    assert str(backend)==pre['backend']['path'] and sha(backend)==pre['backend']['sha256']
    cases={}
    for condition in ('normal','local'):
        for policy in ('control','treatment'):
            name=policy+'-'+condition;path=out/name;assert not path.exists()
            cases[name]=dict(output=str(path),config=str(prepared/('service-control.json' if policy=='control' else 'service-multicall.json')),
                protocol=str(prepared/('rdma-multicall-'+condition+'.json')),topology=str(prepared/'topology'))
    frozen=dict(passed=True,network_executed=False,platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        backend=pre['backend'],loaded_ns3_libraries=pre['reused_ns3_libraries'],cases=cases,prepared=str(prepared),
        prepared_sha256={str(path.relative_to(prepared)):sha(path) for path in prepared.rglob('*') if path.is_file()},
        source_lineage=load(out/'source-lineage.json'),source_manifest_sha256=sha(out/'source-manifest.json'),
        method_sha256=sha(Path(__file__).with_name('protocol.md')),preparer_sha256=sha(Path(__file__)),
        logical_requests=dict(target=4,healthy=11),simulated_endpoints=128,ep_sizes=[32]*4,
        fault_target=load(prepared/'multicall-preparation.json')['fault_target'],new_networks_planned=4)
    save(out/'frozen.json',frozen);print(json.dumps(dict(passed=True,network_executed=False,requests=15,
        source_kind=m['source']['kind'],remote_revision=None,fault_target=frozen['fault_target'])))

if __name__=='__main__':main()
