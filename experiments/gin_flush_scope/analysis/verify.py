"""Independent closed-form and partial-order check, without importing study logic."""
import argparse
from copy import deepcopy
import hashlib
from itertools import permutations
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_bytes())


def check(value):
    assert len(value['configurations']) == 6
    seen = set()
    for row in value['configurations']:
        ep,stride = row['ep'],row['stride']
        assert (ep,stride) not in seen
        seen.add((ep,stride))
        assert ep in (64,128,256) and stride in (1,8)
        assert row['provider_peers'] * stride == ep and row['domain_gpus'] == 8
        assert row['max_lane_scan_count'] == (ep//stride+31)//32
        flat = [peer for scan in row['lane_scan_lists'] for peer in scan]
        assert sorted(flat) == list(range(ep//stride))
        for lane,scan in enumerate(row['lane_scan_lists']):
            assert scan == [p for p in range(ep//stride) if p%32==lane]
        active = [p for p in range(ep//stride) if p>0 and (p*stride)%8==0]
        assert active == row['active_remote_provider_peers']
        maximum = max(sum(peer%32==lane for peer in active) for lane in range(32))
        assert maximum == row['max_remote_rail_peers_per_lane']
        assert row['same_lane_late_active_peer_exists'] == (maximum>1)
    for row in value['context_sharing']:
        qps = row['allocated_context_indices']
        assert qps in (1,2,4) and row['max_channels_per_context'] == 4//qps
        expected = ([0,0,0,0] if qps==1 else [0,0,1,1] if qps==2 else [0,2,1,3])
        assert [r['context'] for r in row['channels']] == expected
        assert all(r['mode']==('GPU' if qps==1 else 'CTA') for r in row['channels'])
    # Enumerate linear extensions by transitive closure, independently of mutable queue state.
    nodes = ('A_sample_first','A_complete_first','A_sample_later','B_reserve_later','B_complete_later')
    edges = {(nodes[0],n) for n in nodes[1:]} | {(nodes[1],nodes[2]),(nodes[3],nodes[4])}
    while True:
        expanded = edges | {(a,d) for a,b in edges for c,d in edges if b==c}
        if expanded==edges:
            break
        edges=expanded
    valid = {order for order in permutations(nodes) if all(order.index(a)<order.index(b) for a,b in edges)}
    assert len(valid)==6 and {tuple(r['events']) for r in value['extensions']}==valid
    for row in value['extensions']:
        positions = {event:i for i,event in enumerate(row['events'])}
        captured = positions['B_reserve_later'] < positions['A_sample_later']
        pending = captured and positions['A_sample_later'] < positions['B_complete_later']
        assert row['captured_B']==captured and row['B_pending_at_sample']==pending
        assert row['entry_snapshot_captures_B'] is False
        assert row['required_completion_nodes']==['A_complete_first']+(['B_complete_later'] if captured else [])
    assert value['captured_extensions']==5 and value['pending_extensions']==2
    assert value['same_peer_preexisting_other_producer']==dict(
        shared_context=dict(sampled_prefix=2,waits_for_B=True),
        separate_context=dict(sampled_prefix=1,waits_for_B=False))
    assert value['rejected_large_ep']==[dict(ep=ep,domain_gpus=8,scaleout_domains=ep//8,
        accepted_by_fixed_hybrid_kernel=False) for ep in (512,1024)]
    assert value['dynamic_resource_DAG_can_bind_sampled_prefix'] is True
    assert value['network_runs']==value['GPU']==value['new_innovations_established']==0
    assert value['measured_latency_benefit'] is value['measured_simulator_speedup'] is None


def execute(stage, output):
    assert load(stage/'state.json')['status']=='completed'
    source = load(stage/'observed/sources.json')
    assert len(source['files'])==7
    for row in source['files']:
        body = (stage/row['saved_path']).read_bytes()
        assert hashlib.sha256(body).hexdigest()==row['sha256']
        assert hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()==row['git_blob_sha']
    value = load(stage/'analysis.json')
    check(value)
    locations = {(r['project'],r['path']):stage/r['saved_path'] for r in source['files']}
    for claim in value['source_claims']:
        lines=locations[claim['project'],claim['path']].read_text().splitlines()
        assert claim['anchor'] in lines[claim['line']-1]
        if claim['path'].endswith('gin_gdaki.h') and ('for (int peer' in claim['anchor'] or '&qps[peer]' in claim['anchor']):
            prefix='\n'.join(lines[:claim['line']])
            assert prefix.rfind('static ncclResult_t flushImplModeCore(')>prefix.rfind('static void flushImplMode(')
    assert len(value['source_claims'])==18
    rejected=[]
    for kind in ('scope','scan_count','captured_prefix','source'):
        changed=deepcopy(value)
        if kind=='scope':
            changed['same_peer_preexisting_other_producer']['shared_context']['waits_for_B']=False
        elif kind=='scan_count':
            changed['configurations'][0]['max_lane_scan_count']=1
        elif kind=='captured_prefix':
            changed['extensions'][1]['captured_B']=not changed['extensions'][1]['captured_B']
        else:
            row=source['files'][0]
            body=(stage/row['saved_path']).read_bytes()+b'corrupt'
            assert hashlib.sha256(body).hexdigest()!=row['sha256']
            rejected.append(kind)
            continue
        try:
            check(changed)
        except AssertionError:
            rejected.append(kind)
        else:
            raise AssertionError('tamper not rejected: '+kind)
    result=dict(passed=True,source_blobs_checked=7,source_anchors_checked=18,
        provider_configurations_checked=6,linear_extensions_checked=6,tamper_classes_rejected=rejected,
        independent_methods='closed-form lane congruence and transitive-closure linear extensions',
        native_provider_executed=False,GPU_executed=False,network_executed=False)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    execute(args.stage.resolve(),args.output.resolve())
