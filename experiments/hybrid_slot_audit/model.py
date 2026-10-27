"""Source-formula slot/epilogue interpreter; no CUDA or timing execution."""
from collections import defaultdict
from .common import key


def interpret(config,plan,declared,sequence,*,wrong_global_counter=False):
    scaleup=plan['ranks_per_domain'];domains=plan['domains'];channels=plan['channels_per_rank']
    maximum=config['execution']['max_tokens_per_rank'];capacity=domains*maximum
    topk=config['model']['top_k'];experts_per_rank=config['model']['experts']//plan['ranks']
    hidden=2*config['model']['hidden_size'];metadata_bytes=12*topk+4
    stride=((hidden+((metadata_bytes+31)//32)*32+63)//64)*64
    dispatch={r['id']:r for r in plan['dispatch']}
    positions=defaultdict(int);counters=defaultdict(int);sent=defaultdict(int)
    allocations=[];forward_metadata={k:[] for k in declared};buffer={}
    for forwarder in sequence:
        ingress,channel=map(int,forwarder.split(':'))
        entry=declared[forwarder][positions[forwarder]];positions[forwarder]+=1
        name=entry['dispatch'];d=dispatch[name];source,token=d['source'],d['token']
        choices=config['calls'][0]['tokens'][source][token]
        target_lanes=[e//experts_per_rank if e//experts_per_rank<scaleup else -1 for e in choices]
        slot_lanes=[-1]*topk;link_lanes=[-1]*topk
        for target in sorted(set(target_lanes)-{-1}):
            lanes=[i for i,gpu in enumerate(target_lanes) if gpu==target];master=lanes[0]
            counter=('wrong-global',target) if wrong_global_counter else (ingress,target)
            slot=counters[counter];counters[counter]+=1
            ordinal=sent[ingress,channel,target];sent[ingress,channel,target]+=1
            for lane in lanes:
                link_lanes[lane]=(channel*(domains*((maximum+channels-1)//channels)+1)+ordinal)*scaleup+ingress
            slot_lanes[master]=slot
            row=dict(id=name+'.gpu'+str(target),dispatch=name,ingress=ingress,target=target,channel=channel,
                atomic_owner=ingress,slot=slot,partition=ingress,byte_offset=(ingress*capacity+slot)*stride,
                linked_ordinal=ordinal,topk_positions=lanes,operand_bytes=stride,
                path='ingress_self_copy' if ingress==target else 'intra_domain_forward',
                source_global_token=source*maximum+token,master_topk=master)
            address=(target,row['byte_offset']);assert address not in buffer
            buffer[address]=row;allocations.append(row)
        forward_metadata[forwarder].append(dict(dispatch=name,source_global_token=source*maximum+token,
            last_in_chunk=entry['last_in_chunk'],target_lanes=target_lanes,slot_lanes=slot_lanes,link_lanes=link_lanes))
    assert all(positions[k]==len(v) for k,v in declared.items())
    # Source copy epilogue scans sender partitions and concrete slots, then builds inverse lists.
    epilogue=[];linked=[];by_epilogue={};linked_map={}
    for target in range(scaleup):
        output_row=0
        for ingress in range(scaleup):
            count=sum(a['ingress']==ingress and a['target']==target for a in allocations)
            for slot in range(count):
                a=buffer[target,(ingress*capacity+slot)*stride]
                row=dict(target=target,row=output_row,dispatch=a['dispatch'],ingress=ingress,slot=slot,
                    source_global_token=a['source_global_token'],src_metadata_slot_topk=slot*topk+a['master_topk'])
                epilogue.append(row);by_epilogue[target,output_row]=row
                link=dict(target=target,channel=a['channel'],ordinal=a['linked_ordinal'],ingress=ingress,row=output_row)
                linked.append(link);linked_map[target,a['channel'],a['linked_ordinal'],ingress]=output_row
                output_row+=1
        for ingress in range(scaleup):
            for channel in range(channels):
                linked.append(dict(target=target,channel=channel,ordinal=sent[ingress,channel,target],ingress=ingress,row=-1))
    # Walk inverse lists with per-channel counters, associating each contribution back to its original token.
    projection=[]
    for forwarder,rows in sorted(forward_metadata.items()):
        ingress,channel=map(int,forwarder.split(':'));used=defaultdict(int)
        for row in rows:
            contributors=[]
            for target in sorted(set(row['target_lanes'])-{-1}):
                ordinal=used[target];used[target]+=1
                i=linked_map[target,channel,ordinal,ingress]
                read=by_epilogue[target,i]
                assert read['dispatch']==row['dispatch'] and read['source_global_token']==row['source_global_token']
                contributors.append(dict(target=target,topk_positions=[i for i,gpu in enumerate(row['target_lanes']) if gpu==target]))
            projection.append(dict(forwarder=forwarder,dispatch=row['dispatch'],contributors=contributors,
                                   last_in_chunk=row['last_in_chunk']))
    return dict(schema_version='hybrid-slot-interpreter/1',scope='declared token-step allocation and inverse association only',
        stride_bytes=stride,capacity_per_sender_partition=capacity,allocations=allocations,
        forward_metadata=forward_metadata,epilogue=epilogue,
        linked_list=sorted(linked,key=lambda r:(r['target'],r['channel'],r['ingress'],r['ordinal'])),
        inverse_projection=projection,network_executed=False,gpu_executed=False)
