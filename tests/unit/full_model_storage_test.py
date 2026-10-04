"""Full-model storage changes preserve values, ownership and tamper rejection."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from moe_ub_cosim.full_model import build_full_model, compose_full_model, model_structure, validate_full_model
from moe_ub_cosim.multilayer import build_children
from moe_ub_cosim.plan import RunIdentities

ROOT=Path(__file__).resolve().parents[2]


def cases():
    root=ROOT/'fixtures/m10_full_model'
    group=json.loads((root/'service.json').read_text())['groups'][0]
    template=json.loads((root/'layers.json').read_text())['groups'][0]['layers'][0]
    for name,kinds,shared,batch,chunk in [
        ('four-block',['dense','moe','moe','moe'],1,1,8192),
        ('dense-between',['moe','dense','moe'],0,3,257),
        ('chunked-batch',['dense']*2+['moe']*6,1,32,257),
        ('single-moe',['moe'],1,2,8192)]:
        model=deepcopy(group['model_spec']);profile=deepcopy(group['full_model_profile'])
        blocks=[];costs=[];configs=[];provenance=[]
        for lid,kind in enumerate(kinds):
            index=0 if kind=='dense' else 1
            block=deepcopy(model['layers'][index]);block['layer_id']=lid;blocks.append(block)
            cost=deepcopy(profile['layers'][index]);cost['layer_id']=lid;costs.append(cost)
            if kind=='moe':
                block.update(shared_experts=shared,shared_intermediate_size=256 if shared else 0)
                cost['per_token_ns']['shared_expert']=500 if shared else 0
                cost['per_token_ns']['shared_routed_add']=29 if shared else 0
                c=deepcopy(template);c.update(layer_id=lid,chunk_bytes=chunk,
                    batch_limits=dict(max_num_seqs=batch,max_num_batched_tokens=batch))
                c['cann_profile']['window_bytes']=8*2**20
                token=c['tokens'][0];c['tokens']=[]
                for i in range(batch):
                    t=deepcopy(token);t.update(layer_id=lid,request_id=f'synthetic-{i}',token_index=2+i)
                    c['tokens'].append(t)
                configs.append(c)
                provenance.append([dict(kind='synthetic_storage_test',layer_id=lid,records=list(range(batch)))])
        model['layers']=blocks;profile['layers']=costs;profile['model_structure']=model_structure(model)
        yield name,dict(configs=configs,provenance=provenance,model=model,profile=profile)


def build(bundle,builder=build_full_model):
    return builder(bundle['configs'],RunIdentities(transfer=2**40,task=2000,tag=4000),
                   bundle['provenance'],bundle['model'],bundle['profile'])


def digest(plan):
    h=hashlib.sha256()
    for piece in json.JSONEncoder(separators=(',',':')).iterencode(plan):h.update(piece.encode())
    return h.hexdigest()


class FullModelStorageTest(unittest.TestCase):
    def test_baseline_values_order_and_input_ownership(self):
        expected=json.loads((ROOT/'fixtures/m10_full_model_storage/golden.json').read_text())['cases']
        actual={}
        for name,bundle in cases():
            with self.subTest(case=name):
                original=deepcopy(bundle);plan=build(bundle)
                self.assertEqual(bundle,original)
                actual[name]=digest(plan);self.assertEqual(actual[name],expected[name])
                # Caller-owned model, configs and provenance remain detached.
                bundle['configs'][0]['tokens'][0]['expert_ids'].clear()
                bundle['provenance'][0][0]['records'].clear()
                bundle['model']['layers'].clear();bundle['profile']['layers'].clear()
                self.assertEqual(digest(plan),actual[name])
        self.assertEqual(set(actual),set(expected))

    def test_public_composition_copies_child_records(self):
        _,b=next(cases());children=build_children(b['configs'],b['provenance'])
        original=deepcopy(children);p=compose_full_model(children,b['model'],b['profile'])
        self.assertEqual(children,original)
        before=digest(p)
        children[0]['transfers'][0]['sources'].clear()
        children[0]['rank_graphs'][0]['nodes'].clear()
        children[0]['input_provenance']['route_records'][0]['records'].clear()
        self.assertEqual(digest(p),before)

    def test_validation_readonly_and_independent_mutable_records(self):
        _,b=next(cases());p=build(b);before=digest(p)
        validate_full_model(p);self.assertEqual(digest(p),before)
        # Child and merged records must not share mutable nested data, or a
        # mutation could change both the input and expected comparison value.
        changes=[
            lambda p:p['transfers'][0]['sources'].clear(),
            lambda p:p['transfers'][0]['pair_byte_range'].__setitem__(0,99),
            lambda p:p['transfers'][0].update(send_node_id=999),
            lambda p:p['layers'][0]['transfers'][0]['sources'].clear(),
            lambda p:p['layers'][0]['rank_graphs'][0]['nodes'][0].update(duration_ns=999),
            lambda p:next(n for n in p['rank_graphs'][0]['nodes'] if n['deps'])['deps'].clear(),
            lambda p:p['rank_graphs'][0]['model_stages'][0].update(duration_ns=999),
        ]
        for i,change in enumerate(changes):
            q=deepcopy(p);change(q);bad=digest(q)
            with self.subTest(mutation=i),self.assertRaises(ValueError):validate_full_model(q)
            self.assertEqual(digest(q),bad,'Rejected plan was mutated by validation')
        self.assertEqual(digest(p),before)


if __name__=='__main__':unittest.main()
