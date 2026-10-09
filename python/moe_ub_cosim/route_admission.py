"""Validate future single-token CANN routes without constructing disposable DAGs.

Actual scheduled batches still go through build_plan/build_full_model and their
complete reconstruction validators before export and backend installation.
"""
from collections import Counter
from copy import deepcopy

from .cann import TEMPLATE,stage_durations
from .plan import validate_config,validate_token,require
from .resources import cann_usage,check_graph


class CannRouteAdmission:
    def __init__(self,layer):
        self.layer=deepcopy(layer);validate_config(self.layer)
        require(layer['template'] in (TEMPLATE,'cann_a3_v850_native_v1','cann_a3_v850_store_resources_v1'),'Route arithmetic requires CANN layout')
        self.a3=layer['template']=='cann_a3_v850_store_resources_v1'
        if self.a3:
            from .a3 import layout_config
            self.cost_layer=layout_config(layer)
        else:self.cost_layer=self.layer
        self.ranks=set(layer['rank_ids']);self.owners=self.layer['expert_placement']
        self.chips={r['rank_id']:r['chip_id'] for r in layer.get('deployment',{}).get('ranks',[])}
        self.base=cann_usage(dict(layer,tokens=[]))
        h=layer['hidden_dim'];chunk=layer['chunk_bytes']
        self.remote_chunks=((h+31)//32*32+44+chunk-1)//chunk+(2*h+chunk-1)//chunk+1
        native=layer.get('native_profile',{})
        self.remote_chunks+=(((h+31)//32*32+44+chunk-1)//chunk)*int(native.get('dispatch')=='LOAD')
        self.remote_chunks+=((2*h+chunk-1)//chunk)*int(native.get('combine')=='LOAD')
        # Every rank always checks all experts' status, even with no Token.
        stage_durations(self.cost_layer,0,0,0,0)

    def check(self,token):
        c=self.layer;validate_token(c,token);src=token['src_rank']
        require(src in self.ranks,'Token source outside group')
        destinations=Counter(self.owners[e] for e in token['expert_ids'])
        near=Counter();remote=0
        for dst,count in destinations.items():
            if src==dst:continue
            if self.chips and self.chips[src]==self.chips[dst]:near[dst]+=count
            else:remote+=count
        near_count=sum(near.values());fabric=self.base['fabric_transfers']+remote*self.remote_chunks
        local=self.base['local_transfers']+3*near_count
        prep=c.get('deployment',{}).get('local_prepare_ns_per_vector',{})
        nodes=2*(fabric+local)+len(self.ranks)+2+2*len(destinations)+bool(c['compute_profile_ns']['next_layer'])
        nodes+=bool(near_count and prep.get('dispatch',0))
        nodes+=len(near) if prep.get('combine',0) else 0
        if self.a3:
            cores=c['cann_profile']['platform_aiv_cores']
            nodes=len(c['rank_ids'])*(7+cores+min(cores,len(c['expert_placement'])))+6*c['top_k']+1
            nodes+=(3+int(c['cann_profile']['copy_fence']=='data_ready'))*(fabric+local)
            nodes+=len(c['rank_ids']) if c['compute_profile_ns']['next_layer'] else 0
        usage=check_graph(dict(transfers=fabric+local,fabric_transfers=fabric,local_transfers=local,nodes=nodes),
                          c.get('resource_budget'))
        for rank in destinations.keys()|{src}:
            stage_durations(self.cost_layer,int(rank==src),destinations[rank],near_count if rank==src else 0,near[rank],rank)
        return usage
