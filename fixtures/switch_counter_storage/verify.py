"""Offline exact-result, physical packet and counter-storage acceptance."""
import argparse
from collections import Counter,defaultdict,deque
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

# Independent functionality auditors; no experiment or platform execution code.
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from fixtures.physical_nic_binding.checks import audit as packet_audit
from fixtures.persistent_scale.audit import audit as capacity_audit


def load(path):return json.loads(path.read_bytes())
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
def need(ok,why):
    if not ok:raise ValueError(why)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def dynamic(result):
    value=deepcopy(result)
    value.pop('switch_counter_storage',None)
    # Only runtime paths vary. Preserve library identities and order.
    value['loaded_libraries']=[Path(p).name for p in value['loaded_libraries']]
    return value


def counters(topology,result,mode):
    value=result['switch_counter_storage'];need(value['mode']==mode,'storage mode')
    degree=Counter()
    for edge in topology['links']:degree.update((edge['src'],edge['dst']))
    expected={n['id'] for n in topology['nodes'] if n['kind']!='gpu'}
    need({r['node'] for r in value['switches']}==expected and len(value['switches'])==len(expected),'switch coverage')
    actual_total=0;snapshot=[]
    for row in value['switches']:
        actual=degree[row['node']]+1;slots=1025 if mode=='legacy_dense' else actual
        need(row['actual_device_slots']==actual and row['storage_slots']==slots and
            row['payload_bytes']==slots*slots*8*4,'independent matrix size')
        need(row['nonzero']==sorted(row['nonzero']) and len({tuple(x[:3]) for x in row['nonzero']})==len(row['nonzero']),'snapshot order/duplicates')
        for ingress,egress,priority,size in row['nonzero']:
            need(0<=ingress<actual and 0<=egress<actual and 0<=priority<8 and 0<size<2**32,'counter domain')
            if result['drained']:need(priority!=3,'data attribution after drain')
        actual_total+=row['payload_bytes']
        snapshot.append(dict(node=row['node'],actual_device_slots=actual,nonzero=row['nonzero']))
    need(actual_total==value['payload_bytes'],'total matrix bytes')
    return snapshot


def paired(topology,legacy,port):
    need(dynamic(legacy)==dynamic(port),'dynamic results differ')
    need(counters(topology,legacy,'legacy_dense')==counters(topology,port,'port_dense'),'counter snapshots differ')


def control_counters(result):
    """Reconstruct retained q0 attribution from switch arrivals/enqueues."""
    cap=result['packet_capture'];wire=cap['wire_headers'];arrivals=defaultdict(deque);expected=Counter()
    for i,row in enumerate(cap['events']):
        _,node,port,event,uid,_,proto,seq,size,*_=row
        if node<result['gpu_count'] or proto==17:continue
        header=wire['headers'][wire['event_header_indices'][i]]
        key=node,uid,proto,seq,size,tuple(header)
        if event==1:arrivals[key].append(port)
        elif event==3:
            need(arrivals[key],'control enqueue without arrival')
            expected[node,arrivals[key].popleft(),port,0]+=size
    actual=Counter()
    for switch in result['switch_counter_storage']['switches']:
        for ingress,egress,q,size in switch['nonzero']:
            if q==0:actual[switch['node'],ingress,egress,q]=size
    need(actual==expected,'independent q0 attribution mismatch')
    return dict(nonzero_cells=len(actual),attributed_bytes=sum(actual.values()))


def verify_small(run):
    topology=load(run/'small-topology/topology.json')
    default=load(run/'small-default/result.json');old=load(run/'small-old/result.json')
    legacy=load(run/'small-legacy_dense/result.json');port=load(run/'small-port_dense/result.json')
    need('switch_counter_storage' not in old and 'switch_counter_storage' not in default,'default result shape changed')
    need(dynamic(old)==dynamic(default)==dynamic(legacy),'old/default compatibility')
    paired(topology,legacy,port)
    healthy=packet_audit(topology,load(run/'inputs/small-port_dense.json'),port)
    healthy['independent_control_counters']=control_counters(port)
    need(port['passed'] and healthy['cnp_packets']>0 and healthy['shared_queue_ports'],'healthy congestion scope')
    return healthy


def verify(run,full=True):
    healthy=verify_small(run)
    topology=load(run/'small-topology/topology.json')
    failure_legacy=load(run/'failure-legacy_dense/result.json')
    failure_port=load(run/'failure-port_dense/result.json')
    paired(topology,failure_legacy,failure_port)
    failure=packet_audit(topology,load(run/'inputs/failure-port_dense.json'),failure_port)
    failure['independent_control_counters']=control_counters(failure_port)
    need(not failure_port['passed'],'partition unexpectedly succeeded')
    summary=dict(passed=True,default_backward_compatible=True,healthy=healthy,failure=failure,
        platform_engineering_only=True,network_optimization_benefit_claimed=False,hardware_calibrated=False)
    if (run/'native-check.json').exists():
        summary['native_object_checks']=load(run/'native-check.json')
        summary['acceptance_complete']=summary['native_object_checks']['passed']
    if full:
        large_topology=load(run/'large-topology/topology.json')
        legacy=load(run/'large-legacy_dense/result.json');port=load(run/'large-port_dense/result.json')
        paired(large_topology,legacy,port)
        summary['large']=capacity_audit(port,load(run/'inputs/large-work.json'))
        old_bytes=legacy['switch_counter_storage']['payload_bytes'];new_bytes=port['switch_counter_storage']['payload_bytes']
        resources={}
        for mode in ('legacy_dense','port_dense'):
            receipt=load(run/('large-'+mode+'-monitor')/'probe.json')
            resources[mode]=dict(wall_seconds=receipt['wall_seconds'],
                sampled_process_tree_peak_rss_bytes=receipt['peak_sampled_group_rss_bytes'])
        summary['storage']=dict(legacy_payload_bytes=old_bytes,port_payload_bytes=new_bytes,
            payload_reduction_fraction=1-new_bytes/old_bytes,observed_resources=resources,
            resource_scope='one invocation per mode, external public-CLI process tree; not a stable speedup estimate or whole-model inference')
    rejected={}
    original=load(run/'small-port_dense/result.json')
    reference=load(run/'small-legacy_dense/result.json')
    for change in ('storage_bytes','counter_value','packet_time','queue_area','callback','capture'):
        modified=deepcopy(original)
        if change=='storage_bytes':modified['switch_counter_storage']['switches'][0]['payload_bytes']+=4
        elif change=='counter_value':
            next(r for r in modified['switch_counter_storage']['switches'] if r['nonzero'])['nonzero'][0][3]+=1
        elif change=='packet_time':modified['packet_capture']['events'][0][0]+=1
        elif change=='queue_area':next(r for r in modified['ports'] if 'queue_metrics' in r)['queue_metrics']['area_byte_ns']+=1
        elif change=='callback':modified['persistent_transport']['work'][0]['acked_ns']+=1
        else:modified['packet_capture']['complete']=False
        try:
            # Test the independent accounting first, then exact paired equality.
            counters(topology,modified,'port_dense')
            packet_audit(topology,load(run/'inputs/small-port_dense.json'),modified)
            paired(topology,reference,modified)
        except (ValueError,AssertionError,KeyError) as error:rejected[change]=str(error)
        else:raise ValueError('accepted corrupted '+change)
    summary['tampering_rejected']=rejected
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--small-only',action='store_true')
    a=p.parse_args();save(a.output,verify(a.run.resolve(),not a.small_only))
